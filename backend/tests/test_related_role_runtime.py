"""Execution contract for full related-role candidate funnels."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

from app.actions._decision_side_effects import apply_decision_side_effects
from app.actions.types import Actor
from app.domains.assessments_runtime.pipeline_service import transition_stage
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.task import Task
from app.services.assessment_invite_delivery import (
    confirm_assessment_invite_provider_success,
)
from app.services.related_role_runtime import run_related_role_cycle
from app.services.sister_role_service import ensure_sister_evaluations
from app.tasks.sister_role_tasks import related_role_agent_cycle
from app.tasks.sister_role_tasks import score_sister_role
from app.platform import config as platform_config


_AGENT_RUN_IDS = {"value": 10_000}


def _assign_agent_run_id(_mapper, _connection, target):
    if target.id is None:
        _AGENT_RUN_IDS["value"] += 1
        target.id = _AGENT_RUN_IDS["value"]


event.listen(AgentRun, "before_insert", _assign_agent_run_id)


def _family(db, *, related_count: int = 2, task: bool = False):
    org = Organization(
        name="Related runtime org",
        slug=f"related-runtime-{id(db)}",
        credits_balance=100_000_000,
    )
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=org.id,
        name="Shared ATS owner",
        source="workable",
        workable_job_id=f"RELATED-RUNTIME-{org.id}",
        workable_job_data={"state": "published"},
        job_spec_text="Original role specification for a production engineering role.",
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"related-runtime-{org.id}@example.com",
        full_name="Related Runtime Candidate",
        cv_text="Production Python, AI systems, reliability, and delivery ownership.",
    )
    db.add_all([owner, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="manual",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()

    roles: list[Role] = []
    evaluations: list[SisterRoleEvaluation] = []
    for index in range(related_count):
        role = Role(
            organization_id=org.id,
            name=f"Full related role {index + 1}",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=owner.id,
            job_spec_text="Related role specification with production AI requirements.",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5_000,
            score_threshold=70,
            auto_reject=False,
            auto_reject_pre_screen=False,
            auto_send_assessment=False,
            auto_resend_assessment=False,
            auto_advance=False,
            auto_skip_assessment=not task,
        )
        db.add(role)
        db.flush()
        if task:
            assessment_task = Task(
                organization_id=org.id,
                name=f"Assessment {index + 1}",
                description="Role-specific assessment",
                duration_minutes=30,
                is_active=True,
            )
            db.add(assessment_task)
            db.flush()
            role.tasks.append(assessment_task)
        evaluation = SisterRoleEvaluation(
            organization_id=org.id,
            role_id=role.id,
            source_application_id=application.id,
            status="done",
            pipeline_stage="review",
            spec_fingerprint=f"related-{index}",
            role_fit_score=85,
        )
        db.add(evaluation)
        roles.append(role)
        evaluations.append(evaluation)
    db.commit()
    return org, owner, application, roles, evaluations


def _pending_decision(db, *, role: Role, application: CandidateApplication):
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="manual",
        status="succeeded",
        model_version="test",
        prompt_version="test",
    )
    db.add(run)
    db.flush()
    decision = AgentDecision(
        organization_id=role.organization_id,
        role_id=role.id,
        application_id=application.id,
        agent_run_id=run.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="Sibling role decision",
        evidence={},
        model_version="test",
        prompt_version="test",
        idempotency_key=f"related-runtime-sibling:{role.id}:{application.id}",
    )
    db.add(decision)
    db.commit()
    return decision


def test_auto_send_creates_assessment_owned_by_related_role(db):
    org, _owner, application, roles, evaluations = _family(db, task=True)
    related = roles[0]
    related.auto_send_assessment = True
    db.commit()
    branch = SimpleNamespace(
        repo_url="https://example.test/repo",
        branch_name="assessment/test",
        clone_command="git clone example",
    )

    with patch(
        "app.actions.send_assessment.get_assessment_creation_gate",
        return_value={"can_create": True, "organization": org},
    ), patch(
        "app.actions.send_assessment.AssessmentRepositoryService.create_assessment_branch",
        return_value=branch,
    ), patch(
        "app.domains.integrations_notifications.invite_flow.dispatch_assessment_invite"
    ) as dispatch:
        result = run_related_role_cycle(db, role=related)
        run_related_role_cycle(db, role=related)

    assessment = db.query(Assessment).one()
    assert result["send_assessment"] == 1
    assert result["auto_executed"] == 1
    assert assessment.role_id == related.id
    assert assessment.application_id == application.id
    assert assessment.candidate_id == application.candidate_id
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    assert db.get(SisterRoleEvaluation, evaluations[0].id).pipeline_stage == "review"

    assert db.query(Assessment).count() == 1
    assert db.query(AgentDecision).count() == 1
    dispatch.assert_called_once()
    assert db.get(SisterRoleEvaluation, evaluations[0].id).pipeline_stage == "review"


def test_provider_confirmation_advances_only_related_assessment_funnel(db):
    _org, _owner, application, roles, evaluations = _family(db, task=True)
    related = roles[0]
    assessment = Assessment(
        organization_id=related.organization_id,
        candidate_id=application.candidate_id,
        task_id=related.tasks[0].id,
        role_id=related.id,
        application_id=application.id,
        token="related-provider-confirmation",
        status=AssessmentStatus.PENDING,
        invite_email_send_generation=1,
        invite_pipeline_transition={"source": "agent"},
    )
    db.add(assessment)
    db.commit()

    result = confirm_assessment_invite_provider_success(
        db,
        assessment_id=assessment.id,
        email_id="email-related-1",
        expected_generation=1,
    )

    assert result["confirmed"] is True
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    assert db.get(SisterRoleEvaluation, evaluations[0].id).pipeline_stage == "invited"


def test_late_invite_confirmation_cannot_regress_a_shared_advance(db):
    _org, _owner, application, roles, evaluations = _family(db, task=True)
    related = roles[0]
    assessment = Assessment(
        organization_id=related.organization_id,
        candidate_id=application.candidate_id,
        task_id=related.tasks[0].id,
        role_id=related.id,
        application_id=application.id,
        token="late-related-provider-confirmation",
        status=AssessmentStatus.PENDING,
        invite_email_send_generation=1,
        invite_pipeline_transition={"source": "agent"},
    )
    db.add(assessment)
    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Candidate already handed off",
    )
    db.commit()

    result = confirm_assessment_invite_provider_success(
        db,
        assessment_id=assessment.id,
        email_id="email-late-related",
        expected_generation=1,
    )

    assert result["confirmed"] is True
    assert db.get(CandidateApplication, application.id).pipeline_stage == "advanced"
    assert db.get(SisterRoleEvaluation, evaluations[0].id).pipeline_stage == "advanced"


def test_expired_assessment_uses_independent_auto_resend_toggle(db):
    _org, _owner, application, roles, _evaluations = _family(db, task=True)
    related = roles[0]
    related.auto_send_assessment = False
    related.auto_resend_assessment = True
    assessment = Assessment(
        organization_id=related.organization_id,
        candidate_id=application.candidate_id,
        task_id=related.tasks[0].id,
        role_id=related.id,
        application_id=application.id,
        token="related-expired",
        status=AssessmentStatus.EXPIRED,
    )
    db.add(assessment)
    db.commit()
    resend_result = SimpleNamespace(status="resent", detail=None)

    with patch(
        "app.actions.resend_assessment_invite.run",
        return_value=resend_result,
    ) as resend:
        result = run_related_role_cycle(db, role=related)

    assert result["resend_assessment_invite"] == 1
    assert result["auto_executed"] == 1
    resend.assert_called_once()
    assert resend.call_args.kwargs["assessment_id"] == assessment.id


def test_reject_recommendations_remain_role_scoped_and_hitl(db):
    _org, _owner, application, roles, evaluations = _family(db)
    for evaluation in evaluations:
        evaluation.role_fit_score = 20
    db.commit()

    for role in roles:
        run_related_role_cycle(db, role=role)

    decisions = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application.id,
            AgentDecision.decision_type == "reject",
        )
        .all()
    )
    assert {decision.role_id for decision in decisions} == {role.id for role in roles}
    assert {decision.status for decision in decisions} == {"pending"}
    assert db.get(CandidateApplication, application.id).application_outcome == "open"


def test_auto_advance_updates_family_and_discards_sibling_cards(db):
    _org, _owner, application, roles, evaluations = _family(db)
    related = roles[0]
    related.auto_advance = True
    sibling = _pending_decision(db, role=roles[1], application=application)
    sibling.status = "processing"
    db.commit()

    result = run_related_role_cycle(db, role=related)

    db.expire_all()
    decisions = db.query(AgentDecision).filter(
        AgentDecision.application_id == application.id
    ).all()
    current = next(row for row in decisions if row.role_id == related.id)
    assert result["advance_to_interview"] == 1
    assert result["auto_executed"] == 1
    assert current.status == "approved"
    assert db.get(AgentDecision, sibling.id).status == "discarded"
    assert db.get(CandidateApplication, application.id).pipeline_stage == "advanced"
    assert {
        row.pipeline_stage
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    } == {"advanced"}


def test_advanced_candidate_is_not_reopened_or_rescored(db):
    _org, _owner, application, roles, evaluations = _family(db)
    related = roles[0]
    original_score = evaluations[0].role_fit_score
    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Candidate handed off",
    )
    db.commit()

    counts = ensure_sister_evaluations(db, related, reset_existing=True)
    db.commit()

    evaluation = db.get(SisterRoleEvaluation, evaluations[0].id)
    assert evaluation.status == "done"
    assert evaluation.role_fit_score == original_score
    assert evaluation.pipeline_stage == "advanced"
    assert counts["done"] >= 1


def test_score_kick_does_not_dispatch_legacy_pending_advanced_row(db):
    _org, _owner, application, roles, evaluations = _family(db)
    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Candidate handed off",
    )
    evaluations[0].status = "pending"
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation"
    ) as dispatch:
        result = score_sister_role.run(roles[0].id)

    assert result["queued"] == 0
    dispatch.assert_not_called()
    db.expire_all()
    evaluation = db.get(SisterRoleEvaluation, evaluations[0].id)
    assert evaluation.status == "done"
    assert evaluation.pipeline_stage == "advanced"


def test_old_advance_idempotency_replay_cannot_advance_only_related_rows(db):
    _org, _owner, application, _roles, evaluations = _family(db)
    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Original advance",
        idempotency_key="shared-advance-once",
    )
    db.flush()
    # Simulate a legacy/manual canonical reopen plus an old related projection.
    # Replaying the original request must be a pure no-op, not a family-only
    # advance that leaves canonical truth in review.
    application.pipeline_stage = "review"
    evaluations[0].pipeline_stage = "review"
    db.flush()

    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Replayed request",
        idempotency_key="shared-advance-once",
    )

    assert application.pipeline_stage == "review"
    assert evaluations[0].pipeline_stage == "review"


def test_related_cycle_claims_application_then_evaluation_rows(db, monkeypatch):
    _org, _owner, _application, roles, _evaluations = _family(db)
    compiled: list[str] = []
    original = Query.with_for_update

    def record_lock(query, *args, **kwargs):
        locked = original(query, *args, **kwargs)
        compiled.append(
            str(locked.statement.compile(dialect=postgresql.dialect()))
        )
        return locked

    monkeypatch.setattr(Query, "with_for_update", record_lock)

    run_related_role_cycle(db, role=roles[0])

    assert any(
        "candidate_applications" in sql and "SKIP LOCKED" in sql
        for sql in compiled
    )
    assert any(
        "FOR UPDATE OF sister_role_evaluations SKIP LOCKED" in sql
        for sql in compiled
    )


def test_owner_manual_advance_reconciles_every_related_projection(db):
    _org, _owner, application, roles, _evaluations = _family(db)
    sibling = _pending_decision(db, role=roles[1], application=application)

    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Original role recruiter advanced the shared candidate",
    )
    db.commit()

    db.expire_all()
    assert db.get(CandidateApplication, application.id).pipeline_stage == "advanced"
    assert db.get(AgentDecision, sibling.id).status == "discarded"
    assert {
        row.pipeline_stage
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    } == {"advanced"}


def test_related_advance_uses_owner_workable_stage_configuration(db, monkeypatch):
    org, owner, application, roles, _evaluations = _family(db)
    related = roles[0]
    owner.workable_stages = [
        {"slug": "applied", "name": "Applied", "kind": "sourced"},
        {
            "slug": "final-interview",
            "name": "Final interview",
            "kind": "interview",
        },
    ]
    related.workable_stages = None
    org.workable_connected = True
    org.workable_access_token = "test-token"
    org.workable_subdomain = "test-workspace"
    org.workable_config = {
        "workable_writeback": True,
        "interview_stage_name": "",
    }
    application.workable_candidate_id = "shared-workable-candidate"
    decision = _pending_decision(db, role=related, application=application)
    decision.decision_type = "advance_to_interview"
    decision.recommendation = "advance_to_interview"
    db.commit()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={"success": True, "action": "move", "code": "ok", "config": {}},
    ) as move, patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable"
    ), patch(
        "app.candidate_graph.agent_episodes.emit_recruiter_action_event"
    ):
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=application,
            org=org,
            role=related,
            disposition="approved",
        )

    assert move.call_args.kwargs["target_stage"] == "final-interview"
    assert move.call_args.kwargs["role"].id == owner.id


def test_related_cycle_materializes_no_decisions_while_workspace_paused(db):
    org, _owner, _application, roles, _evaluations = _family(db)
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    db.commit()

    result = run_related_role_cycle(db, role=roles[0])

    assert result["status"] == "skipped"
    assert result["reason"] == "workspace agent is paused"
    assert db.query(AgentDecision).count() == 0


def test_related_task_stops_before_scoring_while_workspace_paused(db):
    org, _owner, _application, roles, _evaluations = _family(db)
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    db.commit()

    with patch("app.tasks.sister_role_tasks.score_sister_role.run") as score:
        result = related_role_agent_cycle.run(roles[0].id)

    assert result == {
        "status": "skipped",
        "reason": "workspace agent is paused",
        "role_id": roles[0].id,
    }
    score.assert_not_called()


def test_related_cycle_materializes_no_decisions_when_owner_ats_job_closed(db):
    _org, owner, _application, roles, _evaluations = _family(db)
    owner.workable_job_data = {"state": "closed"}
    db.commit()

    result = run_related_role_cycle(db, role=roles[0])

    assert result["status"] == "skipped"
    assert result["reason"] == "linked workable job is not live"
    assert db.query(AgentDecision).count() == 0
