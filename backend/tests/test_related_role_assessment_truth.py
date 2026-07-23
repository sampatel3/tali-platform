"""Ground truth for related-role assessment identity and frozen scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.mcp.catalog import (
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
    PUBLIC_MCP,
    TAALI_CHAT,
)
from app.mcp.shared_reads import dispatch_shared_read
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.task import Task
from app.models.user import User
from app.services.decision_role_context import related_decision_staleness
from app.services.related_role_rescreen_service import (
    rescreen_related_role_candidates,
)
from app.services.related_role_runtime import run_related_role_cycle
from app.services.sister_role_service import text_fingerprint
from tests.conftest import auth_headers
from tests.task_contract_helpers import valid_task_definition


_RUN_IDS = {"value": 4_000_000}


def _assign_agent_run_id(_mapper, _connection, target) -> None:
    if target.id is None:
        _RUN_IDS["value"] += 1
        target.id = _RUN_IDS["value"]


event.listen(AgentRun, "before_insert", _assign_agent_run_id)


def _world(
    db,
    *,
    organization: Organization | None = None,
    role_count: int = 1,
    with_task: bool = False,
) -> dict:
    organization = organization or Organization(
        name="Related assessment truth",
        slug=f"related-assessment-truth-{id(db)}-{_RUN_IDS['value']}",
        credits_balance=100_000_000,
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name="ATS owner",
        source="workable",
        workable_job_id=f"ASSESSMENT-TRUTH-{organization.id}",
        workable_job_data={"state": "published"},
        job_spec_text="ATS transport specification.",
    )
    evidence_role = Role(
        organization_id=int(organization.id),
        name="Candidate evidence role",
        source="manual",
        job_spec_text="Candidate evidence specification.",
    )
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"assessment-truth-{organization.id}-{_RUN_IDS['value']}@example.test",
        full_name="Assessment Truth Candidate",
        cv_text="Production Python, distributed systems, and reliable AI delivery.",
    )
    db.add_all([owner, evidence_role, candidate])
    db.flush()
    source = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(evidence_role.id),
        source="manual",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    transport = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="workable",
        pipeline_stage="review",
        pipeline_stage_source="sync",
        application_outcome="open",
        cv_text=candidate.cv_text,
        workable_candidate_id=f"assessment-transport-{candidate.id}",
    )
    db.add_all([source, transport])
    db.flush()

    roles: list[Role] = []
    evaluations: list[SisterRoleEvaluation] = []
    for index in range(role_count):
        role = Role(
            organization_id=int(organization.id),
            name=f"Independent related role {index + 1}",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=int(owner.id),
            job_spec_text=(
                f"Independent production engineering specification {index + 1}."
            ),
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5_000,
            score_threshold=70,
            auto_reject_threshold_mode="manual",
            auto_reject=False,
            auto_reject_pre_screen=False,
            auto_send_assessment=False,
            auto_resend_assessment=False,
            auto_advance=False,
            auto_skip_assessment=not with_task,
        )
        db.add(role)
        db.flush()
        if with_task:
            task = Task(
                **valid_task_definition(
                    task_key=f"assessment-truth-{organization.id}-{index}",
                    name=f"Assessment truth task {index + 1}",
                ),
                organization_id=int(organization.id),
                is_active=True,
            )
            db.add(task)
            db.flush()
            role.tasks.append(task)
        evaluation = SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(role.id),
            candidate_id=int(candidate.id),
            source_application_id=int(source.id),
            ats_application_id=int(transport.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint=text_fingerprint(role.job_spec_text),
            cv_fingerprint=text_fingerprint(source.cv_text),
            role_fit_score=90,
            summary="Grounded role-local evidence.",
            details={"engine_version": "2.1.0", "prompt_version": "holistic_v2"},
            model_version="offline-test",
            prompt_version="offline-test",
            scored_at=datetime.now(timezone.utc),
        )
        db.add(evaluation)
        roles.append(role)
        evaluations.append(evaluation)
    db.commit()
    return {
        "organization": organization,
        "owner": owner,
        "evidence_role": evidence_role,
        "candidate": candidate,
        "source": source,
        "transport": transport,
        "roles": roles,
        "evaluations": evaluations,
    }


def _assessment(
    db,
    world: dict,
    *,
    role: Role,
    status: AssessmentStatus = AssessmentStatus.COMPLETED,
    assessment_score: float | None = 10,
    taali_score: float | None = 82,
    scoring_partial: bool = False,
    scoring_failed: bool = False,
) -> Assessment:
    task_id = int(role.tasks[0].id) if role.tasks else None
    row = Assessment(
        organization_id=int(role.organization_id),
        candidate_id=int(world["candidate"].id),
        task_id=task_id,
        role_id=int(role.id),
        # Deliberately differs from the membership source application.
        application_id=int(world["transport"].id),
        token=f"assessment-truth-{role.id}-{status.value}",
        status=status,
        completed_at=(
            datetime.now(timezone.utc)
            if status
            in {
                AssessmentStatus.COMPLETED,
                AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
            }
            else None
        ),
        assessment_score=assessment_score,
        taali_score=taali_score,
        scoring_partial=scoring_partial,
        scoring_failed=scoring_failed,
        is_voided=False,
    )
    db.add(row)
    db.commit()
    return row


def test_transport_linked_assessment_drives_cycle_staleness_and_suppression(db):
    world = _world(db, with_task=True)
    role = world["roles"][0]
    evaluation = world["evaluations"][0]
    assessment = _assessment(db, world, role=role)

    result = run_related_role_cycle(db, role=role)

    assert result["advance_to_interview"] == 1
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == int(world["source"].id),
        )
        .one()
    )
    assert decision.evidence["assessment_id"] == int(assessment.id)
    assert decision.evidence["assessment_score"] == 10
    assert decision.evidence["assessment_taali_score"] == 82
    assert decision.evidence["taali_score"] == 82
    assert related_decision_staleness(
        db,
        decision,
        evaluation,
        application=world["source"],
        role=role,
    ).is_stale is False

    reviewer = User(
        organization_id=int(role.organization_id),
        email=f"assessment-truth-reviewer-{role.id}@example.test",
        hashed_password="x",
        full_name="Reviewer",
        is_active=True,
        is_verified=True,
    )
    db.add(reviewer)
    db.flush()
    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = int(reviewer.id)
    db.commit()

    assert run_related_role_cycle(db, role=role)["deduplicated"] == 1
    assessment.taali_score = 90
    db.commit()
    stale = related_decision_staleness(
        db,
        decision,
        evaluation,
        application=world["source"],
        role=role,
    )
    assert "assessment_score_shifted" in stale.reasons
    assert run_related_role_cycle(db, role=role)["created"] == 1
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == int(role.id))
        .count()
        == 2
    )


def test_related_restart_voids_transport_linked_assessment_by_candidate(db):
    world = _world(db, role_count=2)
    acting, sibling = world["roles"]
    acting_assessment = _assessment(
        db,
        world,
        role=acting,
        status=AssessmentStatus.PENDING,
        assessment_score=None,
        taali_score=None,
    )
    sibling_assessment = _assessment(
        db,
        world,
        role=sibling,
        status=AssessmentStatus.PENDING,
        assessment_score=None,
        taali_score=None,
    )

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ):
        outcome = rescreen_related_role_candidates(
            db,
            acting,
            reason="logical_membership_restart",
            application_ids=[int(world["source"].id)],
            void_active_assessments=True,
        )

    assert outcome.assessments_voided == 1
    db.refresh(acting_assessment)
    db.refresh(sibling_assessment)
    assert acting_assessment.application_id == int(world["transport"].id)
    assert acting_assessment.is_voided is True
    assert sibling_assessment.is_voided is False


@pytest.mark.parametrize(
    ("scoring_partial", "scoring_failed"),
    [(True, False), (False, True)],
)
def test_incomplete_grading_mode_matches_rest_mcp_and_chat(
    client,
    db,
    scoring_partial,
    scoring_failed,
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    organization = db.get(Organization, int(user.organization_id))
    assert organization is not None
    world = _world(db, organization=organization)
    role = world["roles"][0]
    _assessment(
        db,
        world,
        role=role,
        assessment_score=95,
        taali_score=92,
        scoring_partial=scoring_partial,
        scoring_failed=scoring_failed,
    )

    response = client.get(
        f"/api/v1/roles/{int(role.id)}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    [rest_row] = response.json()
    assert rest_row["taali_score"] is None
    assert rest_row["score_mode"] == "rubric_grading_pending"
    assert rest_row["score_summary"]["assessment_grading_pending"] is True

    for exposure, bound in (
        (PUBLIC_MCP, False),
        (TAALI_CHAT, False),
        (AGENT_CHAT, True),
        (AUTONOMOUS_AGENT, True),
    ):
        arguments = {"limit": 20}
        if not bound:
            arguments["role_id"] = int(role.id)
        kwargs = {
            "exposure": exposure,
            "db": db,
            "principal": user,
        }
        if bound:
            kwargs["bound_role_id"] = int(role.id)
        result = dispatch_shared_read(
            "search_role_candidates",
            arguments,
            **kwargs,
        )
        [row] = result["items"]
        assert row["taali_score"] is None
        assert row["assessment_score"] is None
        assert row["score_mode"] == "rubric_grading_pending"

    filtered = dispatch_shared_read(
        "search_role_candidates",
        {
            "role_id": int(role.id),
            "score_type": "assessment",
            "min_score": 1,
            "limit": 20,
        },
        exposure=PUBLIC_MCP,
        db=db,
        principal=user,
    )
    assert filtered["total"] == 0
    assert filtered["items"] == []


def test_global_multi_related_projection_batches_runtime_truth(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    organization = db.get(Organization, int(user.organization_id))
    assert organization is not None
    world = _world(db, organization=organization, role_count=3)
    for index, role in enumerate(world["roles"]):
        _assessment(
            db,
            world,
            role=role,
            assessment_score=70 + index,
            taali_score=80 + index,
        )
        db.add(
            AgentDecision(
                id=4_500_000 + int(role.id),
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                application_id=int(world["source"].id),
                candidate_id=int(world["candidate"].id),
                decision_type="advance_to_interview",
                recommendation="advance_to_interview",
                status="pending",
                reasoning="Batch projection truth.",
                evidence={},
                model_version="offline-test",
                prompt_version="offline-test",
                idempotency_key=f"batch-projection-{role.id}",
            )
        )
    db.commit()
    db.expire_all()

    statements: list[str] = []

    def capture_select(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = str(statement).strip().lower()
        if normalized.startswith("select"):
            statements.append(normalized)

    assert db.bind is not None
    event.listen(db.bind, "before_cursor_execute", capture_select)
    try:
        response = client.get(
            "/api/v1/applications",
            params={
                "role_ids": ",".join(str(role.id) for role in world["roles"]),
                "include_stage_counts": "false",
                "limit": 20,
            },
            headers=headers,
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", capture_select)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 3
    assessment_statements = [
        statement
        for statement in statements
        if (
            "from assessments" in statement
            and "where assessments.organization_id in" in statement
            and "assessments.role_id in" in statement
            and "assessments.candidate_id in" in statement
        )
    ]
    decision_statements = [
        statement for statement in statements if "from agent_decisions" in statement
    ]
    assert len(assessment_statements) == 1, "\n".join(assessment_statements)
    assert len(decision_statements) == 1, "\n".join(decision_statements)
