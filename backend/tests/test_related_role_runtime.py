"""Execution contract for full related-role candidate funnels."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
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
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.role_criterion import RoleCriterion
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.task import Task
from app.models.user import User
from app.services.assessment_invite_delivery import (
    confirm_assessment_invite_provider_success,
)
from app.services.related_role_runtime import run_related_role_cycle
from app.services.role_threshold_reconciliation import (
    reconcile_role_threshold_decisions,
)
from app.services.role_agent_dispatch import dispatch_role_agent_cycle
from app.services.decision_role_context import related_decision_staleness
from app.services.sister_role_service import ensure_sister_evaluations, text_fingerprint
from app.tasks.sister_role_tasks import related_role_agent_cycle
from app.tasks.sister_role_tasks import score_sister_role
from app.platform import config as platform_config
from tests.task_contract_helpers import valid_task_definition


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
                **valid_task_definition(
                    task_key=f"related-runtime-{org.id}-{index}",
                    name=f"Assessment {index + 1}",
                ),
                organization_id=org.id,
                is_active=True,
            )
            db.add(assessment_task)
            db.flush()
            role.tasks.append(assessment_task)
        evaluation = SisterRoleEvaluation(
            organization_id=org.id,
            role_id=role.id,
            source_application_id=application.id,
            ats_application_id=application.id,
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


def test_related_role_decision_freezes_related_evaluation_summary(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    related = roles[0]
    evaluation = evaluations[0]
    application.cv_match_details = {
        "summary": "OWNER ROLE ONLY: Pre-screen filtered at 25/100."
    }
    evaluation.role_fit_score = 72.0
    evaluation.summary = "RELATED ROLE ONLY: Strong fit for this role."
    evaluation.details = {
        "summary": evaluation.summary,
        "engine_version": "2.1.0",
    }
    db.commit()

    result = run_related_role_cycle(db, role=related)

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == related.id,
            AgentDecision.application_id == application.id,
        )
        .one()
    )
    assert result["advance_to_interview"] == 1
    assert decision.evidence["sister_evaluation_id"] == evaluation.id
    assert decision.evidence["role_fit_score"] == 72.0
    assert decision.evidence["candidate_summary"] == evaluation.summary
    assert "OWNER ROLE ONLY" not in str(decision.evidence)


def test_ownerless_related_role_agent_cycle_uses_local_membership(db):
    _org, _owner, application, roles, evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    role.ats_owner_role_id = None
    evaluations[0].ats_application_id = None
    db.commit()

    result = run_related_role_cycle(db, role=role)

    assert result["status"] == "ok"
    assert result["advance_to_interview"] == 1
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == int(application.id),
        )
        .one()
    )
    assert decision.evidence["ats_transport_linked"] is False


def test_active_related_membership_with_deleted_evidence_still_queues(
    db,
):
    from app.actions import queue_decision

    org, owner, application, roles, evaluations = _family(
        db,
        related_count=1,
    )
    related = roles[0]
    evaluation = evaluations[0]
    later_candidate = Candidate(
        organization_id=int(org.id),
        email=f"later-related-runtime-{org.id}@example.com",
        full_name="Later Related Runtime Candidate",
        cv_text="Production AI delivery and Python platform ownership.",
    )
    db.add(later_candidate)
    db.flush()
    later_application = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(later_candidate.id),
        role_id=int(owner.id),
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=later_candidate.cv_text,
    )
    db.add(later_application)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=int(org.id),
            role_id=int(related.id),
            source_application_id=int(later_application.id),
            ats_application_id=int(later_application.id),
            status="done",
            pipeline_stage="review",
            spec_fingerprint="later-related",
            role_fit_score=82,
        )
    )
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()

    result = run_related_role_cycle(db, role=related)

    assert result["status"] == "ok"
    assert result["created"] == 2
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(related.id),
            AgentDecision.application_id == int(application.id),
        )
        .one()
    )
    assert decision.candidate_id == application.candidate_id
    assert decision.evidence["sister_evaluation_id"] == evaluation.id
    assert decision.evidence["source_application_id"] == application.id
    assert {
        int(row.application_id)
        for row in db.query(AgentDecision)
        .filter(AgentDecision.role_id == int(related.id))
        .all()
    } == {int(application.id), int(later_application.id)}

    replay = run_related_role_cycle(db, role=related)
    assert replay["status"] == "ok"
    assert replay["pending"] == 2
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == int(related.id))
        .count()
        == 2
    )

    # The same soft-deleted physical row is only evidence for the related
    # role. It is no longer a live membership in its ordinary owner role.
    run = (
        db.query(AgentRun)
        .filter(AgentRun.role_id == int(related.id))
        .one()
    )
    with pytest.raises(HTTPException) as deleted_owner:
        queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(owner.id),
            application_id=int(application.id),
            decision_type="advance_to_interview",
            reasoning="Deleted owner application must remain unavailable.",
            evidence={},
            confidence=1.0,
            model_version="offline-test",
            prompt_version="deleted-evidence-ground-truth.v1",
            skip_episode=True,
        )
    assert deleted_owner.value.status_code == 404
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == int(owner.id))
        .count()
        == 0
    )


def test_related_queue_fails_closed_without_exact_live_membership(db):
    from app.actions import queue_decision

    org, _owner, application, roles, evaluations = _family(
        db,
        related_count=1,
    )
    related = roles[0]
    evaluation = evaluations[0]
    run = AgentRun(
        organization_id=int(org.id),
        role_id=int(related.id),
        trigger="manual",
        status="running",
        model_version="offline-test",
        prompt_version="logical-membership-ground-truth.v1",
    )
    unrelated = Role(
        organization_id=int(org.id),
        name="Unrelated ordinary role",
        source="manual",
        job_spec_text="A separate role with no candidate membership.",
    )
    other_org = Organization(
        name="Other tenant",
        slug=f"other-related-queue-{id(db)}",
    )
    db.add_all([run, unrelated, other_org])
    db.flush()
    other_role = Role(
        organization_id=int(other_org.id),
        name="Other tenant role",
        source="manual",
        job_spec_text="A separate tenant's role.",
    )
    db.add(other_role)
    db.commit()
    actor = Actor.agent(int(run.id))

    def _queue(*, organization_id: int, role_id: int):
        return queue_decision.run(
            db,
            actor,
            organization_id=organization_id,
            role_id=role_id,
            application_id=int(application.id),
            decision_type="advance_to_interview",
            reasoning="Only exact live logical membership may authorize this.",
            evidence={},
            confidence=1.0,
            model_version="offline-test",
            prompt_version="logical-membership-ground-truth.v1",
            skip_episode=True,
        )

    with pytest.raises(HTTPException) as wrong_role:
        _queue(organization_id=int(org.id), role_id=int(unrelated.id))
    assert wrong_role.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_tenant:
        _queue(
            organization_id=int(other_org.id),
            role_id=int(other_role.id),
        )
    assert wrong_tenant.value.status_code == 404

    evaluation.deleted_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(HTTPException) as removed_membership:
        _queue(organization_id=int(org.id), role_id=int(related.id))
    assert removed_membership.value.status_code == 404

    evaluation.deleted_at = None
    application.candidate.deleted_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(HTTPException) as deleted_candidate:
        _queue(organization_id=int(org.id), role_id=int(related.id))
    assert deleted_candidate.value.status_code == 404
    assert db.query(AgentDecision).count() == 0


def test_deleted_ordinary_application_stays_blocked_at_action_boundaries(db):
    from app.actions import advance_stage, reject_application, send_assessment

    org, owner, application, _roles, _evaluations = _family(
        db,
        related_count=1,
    )
    recruiter = User(
        organization_id=int(org.id),
        email=f"deleted-action-boundary-{org.id}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
    )
    db.add(recruiter)
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()
    actor = Actor.recruiter(recruiter)

    calls = (
        lambda: advance_stage.run(
            db,
            actor,
            organization_id=int(org.id),
            application_id=int(application.id),
            to_stage="advanced",
        ),
        lambda: reject_application.run(
            db,
            actor,
            organization_id=int(org.id),
            application_id=int(application.id),
        ),
        lambda: send_assessment.run(
            db,
            actor,
            organization_id=int(org.id),
            application_id=int(application.id),
            role_id=int(owner.id),
        ),
    )
    for call in calls:
        with pytest.raises(HTTPException) as blocked:
            call()
        assert blocked.value.status_code == 404

    db.refresh(application)
    assert application.deleted_at is not None
    assert application.pipeline_stage == "review"
    assert application.application_outcome == "open"
    assert db.query(Assessment).count() == 0


def test_passive_shared_input_reset_holds_visible_cards_and_blocks_cycle(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role = roles[0]
    evaluation = evaluations[0]
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
    evaluation.spec_fingerprint = text_fingerprint(role.job_spec_text)
    evaluation.cv_fingerprint = text_fingerprint(application.cv_text)
    application.cv_text = f"{application.cv_text} Added current CV evidence."
    decision = AgentDecision(
        organization_id=role.organization_id,
        role_id=role.id,
        application_id=application.id,
        agent_run_id=run.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="reverted_for_feedback",
        reasoning="Old related-role score",
        evidence={},
        model_version="test",
        prompt_version="test",
        idempotency_key=(
            f"related-input-reset:reverted:{role.id}:{application.id}"
        ),
    )
    db.add(decision)
    db.commit()

    from app.services.sister_role_evaluation_lifecycle import (
        reset_related_evaluations_for_application,
    )

    reset_ids = reset_related_evaluations_for_application(
        db,
        application,
        reason="candidate_cv_replaced",
    )
    db.commit()

    db.expire_all()
    assert reset_ids == [evaluation.id]
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "stale_held"
    assert saved.last_error_code == "shared_inputs_changed"
    assert db.get(AgentDecision, decision.id).status == "reverted_for_feedback"
    report = related_decision_staleness(
        db,
        db.get(AgentDecision, decision.id),
        saved,
        application=application,
        role=role,
    )
    assert report.is_stale is True
    assert report.reasons == ["related_role_inputs_changed"]

    with patch(
        "app.agent_runtime.tool_registry.maybe_auto_execute_decision"
    ) as auto_execute:
        result = run_related_role_cycle(db, role=role)

    assert result == {"status": "ok", "role_id": role.id}
    auto_execute.assert_not_called()
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.status.in_(("pending", "reverted_for_feedback")),
        )
        .count()
        == 1
    )


def test_explicit_shared_input_reset_cancels_processing_before_fresh_cycle(db):
    org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role = roles[0]
    evaluation = evaluations[0]
    decision = _pending_decision(db, role=role, application=application)
    decision.status = "processing"
    db.commit()

    from app.services.sister_role_evaluation_lifecycle import (
        reset_related_evaluations_for_application,
    )
    from app.services.workable_op_runner import _requeue_decision

    assert reset_related_evaluations_for_application(
        db,
        application,
        reason="candidate_cv_replaced",
        queue_for_rescore=True,
    ) == [evaluation.id]
    db.commit()

    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "discarded"
    assert db.get(SisterRoleEvaluation, evaluation.id).status == "pending"
    # The already-published approval worker cannot resurrect the cancelled
    # generation when its deterministic freshness failure is handled.
    _requeue_decision(
        db,
        int(decision.id),
        int(org.id),
        note="stale worker",
    )
    assert db.get(AgentDecision, decision.id).status == "discarded"

    evaluation = db.get(SisterRoleEvaluation, evaluation.id)
    evaluation.status = "done"
    evaluation.role_fit_score = 82.0
    evaluation.spec_fingerprint = text_fingerprint(role.job_spec_text)
    evaluation.cv_fingerprint = text_fingerprint(application.cv_text)
    evaluation.details = {"engine_version": "2.1.0"}
    db.commit()

    result = run_related_role_cycle(db, role=role)
    assert result["created"] == 1
    fresh = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.application_id == application.id,
            AgentDecision.status == "pending",
        )
        .one()
    )
    assert fresh.id != decision.id


def test_owner_advance_does_not_freeze_independent_related_rescore(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    evaluation = evaluations[0]
    evaluation.spec_fingerprint = text_fingerprint(roles[0].job_spec_text)
    evaluation.cv_fingerprint = text_fingerprint(application.cv_text)
    application.cv_text = f"{application.cv_text} New shared CV evidence."
    application.pipeline_stage = "advanced"
    db.commit()

    from app.services.sister_role_evaluation_lifecycle import (
        reset_related_evaluations_for_application,
    )

    assert reset_related_evaluations_for_application(
        db,
        application,
        reason="candidate_cv_replaced",
    ) == [evaluation.id]
    db.commit()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "stale_held"
    assert saved.role_fit_score is None
    assert saved.pipeline_stage == "review"


def test_shared_input_reset_keeps_locally_advanced_evaluation_frozen(db):
    _org, _owner, application, _roles, evaluations = _family(db, related_count=1)
    evaluation = evaluations[0]
    evaluation.pipeline_stage = "advanced"
    original_score = evaluation.role_fit_score
    db.commit()

    from app.services.sister_role_evaluation_lifecycle import (
        reset_related_evaluations_for_application,
    )

    assert application.application_outcome == "open"
    assert application.pipeline_stage != "advanced"
    assert reset_related_evaluations_for_application(
        db,
        application,
        reason="candidate_cv_replaced",
    ) == []
    db.commit()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "done"
    assert saved.pipeline_stage == "advanced"
    assert saved.role_fit_score == original_score


def _role_local_fingerprints(application, role, evaluation):
    evaluation.spec_fingerprint = text_fingerprint(role.job_spec_text)
    evaluation.cv_fingerprint = text_fingerprint(application.cv_text)
    evaluation.details = {"engine_version": "2.1.0"}


def test_related_decision_staleness_tracks_threshold_and_role_inputs(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    role.auto_reject_threshold_mode = "manual"
    criterion = RoleCriterion(
        role_id=role.id,
        text="Production Python",
        bucket="must",
        weight=2.0,
        must_have=True,
    )
    db.add(criterion)
    _role_local_fingerprints(application, role, evaluation)
    db.commit()

    run_related_role_cycle(db, role=role)
    decision = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.application_id == application.id,
    ).one()
    assert related_decision_staleness(
        db, decision, evaluation, application=application, role=role
    ).is_stale is False

    criterion.text = "Production Python and Kubernetes"
    role.score_threshold = 75
    db.flush()
    report = related_decision_staleness(
        db, decision, evaluation, application=application, role=role
    )
    assert "criteria_changed" in report.reasons
    assert "threshold_changed" in report.reasons


def test_related_decision_staleness_uses_role_owned_assessment(db):
    _org, _owner, application, roles, evaluations = _family(
        db, related_count=1, task=True
    )
    role, evaluation = roles[0], evaluations[0]
    _role_local_fingerprints(application, role, evaluation)
    assessment = Assessment(
        organization_id=role.organization_id,
        candidate_id=application.candidate_id,
        task_id=role.tasks[0].id,
        role_id=role.id,
        application_id=application.id,
        token="related-staleness-assessment",
        status=AssessmentStatus.COMPLETED,
        taali_score=90.0,
    )
    db.add(assessment)
    db.commit()

    run_related_role_cycle(db, role=role)
    decision = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.application_id == application.id,
    ).one()
    assessment.taali_score = 50.0
    db.flush()
    report = related_decision_staleness(
        db,
        decision,
        evaluation,
        application=application,
        role=role,
        assessment=assessment,
    )
    assert "assessment_score_shifted" in report.reasons


def test_related_queue_identity_ignores_owner_role_scores(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    evaluation.role_fit_score = 85.0
    _role_local_fingerprints(application, role, evaluation)
    application.pre_screen_score_100 = 20.0
    application.assessment_score_cache_100 = 25.0
    application.taali_score_cache_100 = 30.0
    db.commit()

    run_related_role_cycle(db, role=role)
    decision = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.application_id == application.id,
    ).one()
    assert decision.input_fingerprint["pre_screen_score_at_emit"] is None
    assert decision.input_fingerprint["assessment_score_at_emit"] is None
    assert decision.input_fingerprint["cv_match_score_at_emit"] == 85.0
    assert decision.input_fingerprint["taali_score_at_emit"] == 85.0
    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    application.pre_screen_score_100 = 95.0
    application.assessment_score_cache_100 = 96.0
    application.taali_score_cache_100 = 97.0
    db.commit()

    result = run_related_role_cycle(db, role=role)
    assert result["deduplicated"] == 1
    assert db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.application_id == application.id,
    ).count() == 1


def test_related_human_suppression_ignores_owner_scores_but_releases_on_threshold(db):
    org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    role.auto_reject_threshold_mode = "manual"
    _role_local_fingerprints(application, role, evaluation)
    db.commit()
    run_related_role_cycle(db, role=role)
    decision = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.application_id == application.id,
    ).one()
    reviewer = User(
        email=f"related-reviewer-{org.id}@example.com",
        hashed_password="x",
        full_name="Reviewer",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
    )
    db.add(reviewer)
    db.flush()
    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = reviewer.id
    application.pre_screen_score_100 = 99.0
    application.assessment_score_cache_100 = 99.0
    application.taali_score_cache_100 = 99.0
    db.commit()

    assert run_related_role_cycle(db, role=role)["deduplicated"] == 1
    role.score_threshold = 75
    db.commit()
    assert run_related_role_cycle(db, role=role)["created"] == 1


def test_threshold_reconcile_uses_only_related_membership_score_and_state(db):
    org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    role.auto_reject_threshold_mode = "manual"
    evaluation.role_fit_score = 85.0
    # Deliberately contradictory transport/owner truth: neither the owner's
    # low score nor its terminal funnel state belongs to this logical role.
    application.pre_screen_score_100 = 5.0
    application.cv_match_score = 5.0
    application.pipeline_stage = "advanced"
    application.application_outcome = "rejected"
    db.commit()

    assert run_related_role_cycle(db, role=role)["advance_to_interview"] == 1
    original = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.status == "pending",
    ).one()

    role.score_threshold = 90
    db.commit()
    result = reconcile_role_threshold_decisions(
        db,
        role=role,
        organization_id=org.id,
    )

    db.expire_all()
    assert result["threshold_discarded"] == 1
    assert db.get(AgentDecision, original.id).status == "discarded"
    current = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.status == "pending",
    ).one()
    assert current.decision_type == "reject"
    assert current.evidence["taali_score"] == 85.0
    assert current.evidence["effective_threshold"] == 90.0
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.pipeline_stage == "review"
    assert saved.application_outcome == "open"


def test_threshold_reconcile_preserves_processing_and_resolved_history(db):
    org, _owner, application, roles, _evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    role.auto_reject_threshold_mode = "manual"
    db.commit()
    run_related_role_cycle(db, role=role)
    processing = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.status == "pending",
    ).one()
    processing.status = "processing"
    historical = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application.id,
        decision_type="reject",
        recommendation="reject",
        status="approved",
        reasoning="Immutable prior human action",
        evidence={"effective_threshold": 20.0},
        model_version="test",
        prompt_version="test",
        idempotency_key=f"related-threshold-history:{role.id}:{application.id}",
    )
    db.add(historical)
    role.score_threshold = 95
    db.commit()

    result = reconcile_role_threshold_decisions(
        db,
        role=role,
        organization_id=org.id,
    )

    assert result.get("threshold_discarded", 0) == 0
    assert db.get(AgentDecision, processing.id).status == "processing"
    assert db.get(AgentDecision, historical.id).status == "approved"
    assert db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.status == "pending",
    ).count() == 0


def test_threshold_reconcile_respects_role_pause_until_next_cycle(db):
    org, _owner, _application, roles, _evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    role.auto_reject_threshold_mode = "manual"
    db.commit()
    run_related_role_cycle(db, role=role)
    pending = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.status == "pending",
    ).one()
    role.score_threshold = 95
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "manual"
    db.commit()

    result = reconcile_role_threshold_decisions(
        db,
        role=role,
        organization_id=org.id,
    )

    assert result["status"] == "skipped"
    assert "paused" in result["reason"]
    assert db.get(AgentDecision, pending.id).status == "pending"


def test_auto_send_creates_assessment_owned_by_related_role(db):
    org, _owner, application, roles, evaluations = _family(db, task=True)
    related = roles[0]
    related.auto_send_assessment = True
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()
    with patch(
        "app.actions.send_assessment.get_assessment_creation_gate",
        return_value={"can_create": True, "organization": org},
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
    assert assessment.assessment_repo_url is None
    assert assessment.assessment_branch is None
    assert assessment.clone_command is None
    assert db.get(CandidateApplication, application.id).deleted_at is not None
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


def test_related_assessment_transition_is_versioned_role_scoped_and_idempotent(db):
    _org, _owner, application, roles, evaluations = _family(
        db, related_count=2, task=True
    )
    related, sibling_role = roles
    evaluation = evaluations[0]
    evaluation.pipeline_stage = "invited"
    evaluation.version = 4
    assessment = Assessment(
        organization_id=related.organization_id,
        candidate_id=application.candidate_id,
        task_id=related.tasks[0].id,
        role_id=related.id,
        application_id=application.id,
        token="related-versioned-assessment-transition",
        status=AssessmentStatus.COMPLETED,
        taali_score=88.0,
    )
    db.add(assessment)
    db.commit()
    stale = _pending_decision(db, role=related, application=application)
    sibling = _pending_decision(db, role=sibling_role, application=application)

    from app.services.related_role_application_runtime import (
        transition_related_role_assessment_stage,
    )

    result = transition_related_role_assessment_stage(
        db,
        assessment=assessment,
        to_stage="review",
        source="system",
        idempotency_key="assessment-transition-versioned-once",
        reason="Assessment completed",
    )
    db.commit()

    assert result.handled is True
    assert result.changed is True
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.pipeline_stage == "review"
    assert saved.version == 5
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    assert db.get(AgentDecision, stale.id).status == "discarded"
    assert db.get(AgentDecision, sibling.id).status == "pending"
    event_row = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application.id,
            CandidateApplicationEvent.role_id == related.id,
            CandidateApplicationEvent.idempotency_key
            == "assessment-transition-versioned-once",
        )
        .one()
    )
    assert event_row.from_stage == "invited"
    assert event_row.to_stage == "review"
    assert event_row.effect_status == "confirmed"

    replay = transition_related_role_assessment_stage(
        db,
        assessment=assessment,
        to_stage="review",
        source="system",
        idempotency_key="assessment-transition-versioned-once",
        reason="Assessment completed",
    )
    db.commit()
    assert replay.handled is True
    assert replay.changed is False
    assert db.get(SisterRoleEvaluation, evaluation.id).version == 5


@pytest.mark.parametrize(
    ("terminal_field", "terminal_value"),
    [
        ("deleted_at", datetime.now(timezone.utc)),
        ("application_outcome", "rejected"),
        ("pipeline_stage", "advanced"),
    ],
)
def test_related_cycle_never_recreates_decisions_for_inactive_membership(
    db, terminal_field, terminal_value
):
    _org, _owner, _application, roles, evaluations = _family(
        db, related_count=1
    )
    setattr(evaluations[0], terminal_field, terminal_value)
    db.commit()

    result = run_related_role_cycle(db, role=roles[0])

    assert result == {"status": "ok", "role_id": roles[0].id}
    assert db.query(AgentDecision).count() == 0


def test_late_invite_confirmation_cannot_regress_related_role_advance(db):
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
    from app.services.related_role_action_service import (
        transition_related_role_stage_action,
    )

    transition_related_role_stage_action(
        db,
        application=application,
        acting_role_id=related.id,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Candidate already advanced in this role",
    )
    db.commit()

    result = confirm_assessment_invite_provider_success(
        db,
        assessment_id=assessment.id,
        email_id="email-late-related",
        expected_generation=1,
    )

    assert result["confirmed"] is True
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    assert db.get(SisterRoleEvaluation, evaluations[0].id).pipeline_stage == "advanced"


def test_provider_confirmation_fails_closed_when_related_membership_was_deleted(db):
    _org, _owner, application, roles, evaluations = _family(
        db, related_count=1, task=True
    )
    related = roles[0]
    evaluation = evaluations[0]
    assessment = Assessment(
        organization_id=related.organization_id,
        candidate_id=application.candidate_id,
        task_id=related.tasks[0].id,
        role_id=related.id,
        application_id=application.id,
        token="deleted-membership-provider-confirmation",
        status=AssessmentStatus.PENDING,
        invite_email_send_generation=2,
        invite_pipeline_transition={"source": "agent"},
    )
    evaluation.deleted_at = datetime.now(timezone.utc)
    db.add(assessment)
    db.commit()

    result = confirm_assessment_invite_provider_success(
        db,
        assessment_id=assessment.id,
        email_id="email-deleted-membership",
        expected_generation=2,
    )

    assert result["confirmed"] is True
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    assert db.get(SisterRoleEvaluation, evaluation.id).pipeline_stage == "review"
    held = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application.id,
            CandidateApplicationEvent.role_id == related.id,
            CandidateApplicationEvent.effect_status == "held",
        )
        .all()
    )
    assert held


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


def test_deterministic_auto_reject_resolves_only_the_opted_in_related_role(db):
    _org, _owner, application, roles, evaluations = _family(db)
    opted_in, sibling = roles
    opted_in.auto_reject = True
    for evaluation in evaluations:
        evaluation.role_fit_score = 20
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with patch("app.actions.reject_application.notify_rejection") as provider_reject:
        opted_in_result = run_related_role_cycle(db, role=opted_in)
        sibling_result = run_related_role_cycle(db, role=sibling)

    db.expire_all()
    assert opted_in_result["reject"] == 1
    assert opted_in_result["auto_executed"] == 1
    assert sibling_result["reject"] == 1
    assert "auto_executed" not in sibling_result
    assert db.get(SisterRoleEvaluation, evaluations[0].id).application_outcome == "rejected"
    assert db.get(SisterRoleEvaluation, evaluations[1].id).application_outcome == "open"
    assert db.get(CandidateApplication, application.id).deleted_at is not None
    assert db.get(CandidateApplication, application.id).application_outcome == "open"
    assert {
        (decision.role_id, decision.status)
        for decision in db.query(AgentDecision)
        .filter(AgentDecision.application_id == application.id)
        .all()
    } == {
        (opted_in.id, "approved"),
        (sibling.id, "pending"),
    }
    provider_reject.assert_not_called()


def test_auto_advance_updates_only_the_opted_in_related_role(db):
    _org, _owner, application, roles, evaluations = _family(db)
    related = roles[0]
    related.auto_advance = True
    sibling = _pending_decision(db, role=roles[1], application=application)
    sibling.status = "processing"
    application.deleted_at = datetime.now(timezone.utc)
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
    assert db.get(AgentDecision, sibling.id).status == "processing"
    assert db.get(CandidateApplication, application.id).deleted_at is not None
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    stages_by_role = {
        row.role_id: row.pipeline_stage
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    }
    assert stages_by_role[related.id] == "advanced"
    assert stages_by_role[roles[1].id] == "review"


def test_owner_advance_does_not_freeze_related_role_manual_rescore(db):
    _org, _owner, application, roles, evaluations = _family(db)
    related = roles[0]
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
    assert evaluation.status == "pending"
    assert evaluation.role_fit_score is None
    assert evaluation.pipeline_stage == "review"
    assert counts["pending"] >= 1


def test_owner_advance_does_not_block_pending_related_score_dispatch(db):
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
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ) as dispatch:
        result = score_sister_role.run(roles[0].id)

    assert result["queued"] == 1
    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs == {"evaluation_id": evaluations[0].id}
    db.expire_all()
    evaluation = db.get(SisterRoleEvaluation, evaluations[0].id)
    assert evaluation.status == "pending"
    assert evaluation.pipeline_stage == "review"


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


def test_related_cycle_locks_org_role_before_application_and_evaluation(
    db, monkeypatch
):
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

    lock_order = [
        next(index for index, sql in enumerate(compiled) if marker in sql)
        for marker in (
            "FOR UPDATE OF organizations",
            "FOR UPDATE OF roles",
            "FOR UPDATE SKIP LOCKED",
            "FOR UPDATE OF sister_role_evaluations SKIP LOCKED",
        )
    ]
    assert "candidate_applications" in compiled[lock_order[2]]
    assert lock_order == sorted(lock_order)


def test_direct_related_membership_locks_logical_app_before_ats_transport(db):
    _org, _owner, owner_application, roles, evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    evaluation = evaluations[0]
    direct_application = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=owner_application.candidate_id,
        role_id=role.id,
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
        cv_text="Direct related-role evidence",
    )
    db.add(direct_application)
    db.flush()
    evaluation.candidate_id = owner_application.candidate_id
    evaluation.source_application_id = direct_application.id
    evaluation.ats_application_id = owner_application.id
    db.commit()

    from app.services.sister_role_scoring_generation import (
        locate_sister_score,
        lock_sister_score_rows,
    )

    locator = locate_sister_score(db, evaluation_id=evaluation.id)
    assert locator is not None
    db.rollback()
    locked = lock_sister_score_rows(db, locator=locator, skip_locked=False)

    assert locked is not None
    assert locked.application.id == direct_application.id
    assert locked.application.role_id == role.id
    assert locked.ats_application is not None
    assert locked.ats_application.id == owner_application.id


def test_related_score_inputs_exclude_all_owner_job_context(db):
    _org, owner, application, roles, evaluations = _family(
        db, related_count=2
    )
    role = roles[0]
    evaluation = evaluations[0]
    roles[1].job_spec_text = (
        "A divergent related-role specification requiring Rust and consensus."
    )
    owner.job_spec_text = "OWNER-ONLY role specification that must not leak."
    expected_owner_spec = owner.job_spec_text
    expected_role_spec = role.job_spec_text
    expected_other_role_spec = roles[1].job_spec_text
    application.workable_answers = [
        {
            "question": {"body": "Owner-role salary expectation?"},
            "answer": {"body": "Owner-only answer"},
        }
    ]
    application.workable_comments = [
        {"body": "Owner-only recruiter comment", "member": {"name": "Sam"}}
    ]
    application.workable_activities = [
        {"action": "interview", "stage_name": "Owner technical screen"}
    ]
    application.workable_stage = "Owner interview"
    db.commit()

    from app.services.sister_role_scoring_generation import (
        capture_sister_score_inputs,
        locate_sister_score,
        lock_sister_score_rows,
    )

    locator = locate_sister_score(db, evaluation_id=int(evaluation.id))
    assert locator is not None
    db.rollback()
    locked = lock_sister_score_rows(db, locator=locator, skip_locked=False)
    assert locked is not None

    with patch(
        "app.services.workable_context_service.format_workable_context",
        side_effect=AssertionError("owner job context must not be rendered"),
    ):
        before = capture_sister_score_inputs(locked)
        assert locked.ats_application is not None
        locked.ats_application.workable_answers = [{"answer": "changed"}]
        locked.ats_application.workable_comments = [{"body": "changed"}]
        locked.ats_application.workable_activities = [{"action": "changed"}]
        locked.ats_application.workable_stage = "changed"
        after = capture_sister_score_inputs(locked)

    assert before == after
    assert before.cv_text == application.cv_text
    assert before.job_spec == expected_role_spec
    assert before.job_spec != expected_owner_spec
    assert before.workable_context is None

    db.rollback()
    other_locator = locate_sister_score(
        db, evaluation_id=int(evaluations[1].id)
    )
    assert other_locator is not None
    db.rollback()
    other_locked = lock_sister_score_rows(
        db, locator=other_locator, skip_locked=False
    )
    assert other_locked is not None
    other_inputs = capture_sister_score_inputs(other_locked)
    assert other_inputs.cv_text == before.cv_text
    assert other_inputs.job_spec == expected_other_role_spec
    assert other_inputs.job_spec != before.job_spec
    assert other_inputs.workable_context is None


def test_owner_resync_recovery_never_refreshes_related_score_or_decision(db):
    _org, _owner, application, roles, evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    evaluation = evaluations[0]
    decision = _pending_decision(db, role=role, application=application)
    original = {
        "status": evaluation.status,
        "spec_fingerprint": evaluation.spec_fingerprint,
        "cv_fingerprint": evaluation.cv_fingerprint,
        "role_fit_score": evaluation.role_fit_score,
        "summary": evaluation.summary,
        "details": evaluation.details,
        "history": evaluation.history,
    }

    from app.services.ats_related_role_dispatch import (
        dispatch_related_role_work,
        related_role_work_pending,
    )

    assert related_role_work_pending(db, application) is False
    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation"
    ) as dispatch:
        result = dispatch_related_role_work(db, application)

    dispatch.assert_not_called()
    assert result == {"evaluations": 1, "dispatched": 0}
    db.refresh(evaluation)
    assert {
        "status": evaluation.status,
        "spec_fingerprint": evaluation.spec_fingerprint,
        "cv_fingerprint": evaluation.cv_fingerprint,
        "role_fit_score": evaluation.role_fit_score,
        "summary": evaluation.summary,
        "details": evaluation.details,
        "history": evaluation.history,
    } == original
    db.refresh(decision)
    assert decision.status == "pending"


def test_ownerless_related_scoring_locks_evidence_without_ats_context(db):
    _org, _owner, owner_application, roles, evaluations = _family(
        db, related_count=1
    )
    role = roles[0]
    evaluation = evaluations[0]
    direct_application = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=owner_application.candidate_id,
        role_id=role.id,
        source="manual",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text="Ownerless direct evidence for production AI delivery.",
    )
    db.add(direct_application)
    db.flush()
    role.ats_owner_role_id = None
    evaluation.source_application_id = int(direct_application.id)
    evaluation.ats_application_id = None
    db.commit()

    from app.services.sister_role_scoring_generation import (
        capture_sister_score_inputs,
        locate_sister_score,
        lock_sister_score_rows,
    )

    locator = locate_sister_score(db, evaluation_id=evaluation.id)
    assert locator is not None
    assert locator.ats_owner_role_id is None
    db.rollback()
    locked = lock_sister_score_rows(db, locator=locator, skip_locked=False)

    assert locked is not None
    assert locked.application.id == direct_application.id
    assert locked.ats_application is None
    assert capture_sister_score_inputs(locked).workable_context is None


def test_owner_manual_advance_does_not_mutate_related_role_state(db):
    _org, _owner, application, roles, _evaluations = _family(db)
    sibling = _pending_decision(db, role=roles[1], application=application)

    transition_stage(
        db,
        app=application,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        reason="Original role recruiter advanced this candidate",
    )
    db.commit()

    db.expire_all()
    assert db.get(CandidateApplication, application.id).pipeline_stage == "advanced"
    assert db.get(AgentDecision, sibling.id).status == "pending"
    assert {
        row.pipeline_stage
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    } == {"review"}


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


def test_related_dispatch_keeps_the_rolling_safe_one_argument_payload(db):
    _org, _owner, _application, roles, _evaluations = _family(
        db, related_count=1
    )
    role = roles[0]

    with patch(
        "app.tasks.sister_role_tasks.related_role_agent_cycle.delay"
    ) as dispatch:
        dispatch_role_agent_cycle(role)
        dispatch.assert_called_once_with(role.id)


def test_related_cycle_keeps_local_decisions_when_owner_ats_job_closed(db):
    _org, owner, _application, roles, _evaluations = _family(db)
    owner.workable_job_data = {"state": "closed"}
    db.commit()

    result = run_related_role_cycle(db, role=roles[0])

    assert result["status"] == "ok"
    assert result["advance_to_interview"] == 1
    decision = db.query(AgentDecision).one()
    assert decision.role_id == roles[0].id
    assert decision.status == "pending"
