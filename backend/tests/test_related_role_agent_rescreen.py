"""Ground-truth tests for Agent Chat's role-local related-role re-screen."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.agent_chat import constraints, rescore
from app.models.agent_decision import AgentDecision
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.related_role_rescreen_service import (
    RelatedRoleRescreenResult,
    rescreen_related_role_candidates,
)


def _decision(
    db,
    *,
    role: Role,
    application: CandidateApplication,
    status: str,
    label: str,
) -> AgentDecision:
    row = AgentDecision(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning=f"Old role-local decision {label}",
        evidence={},
        model_version="old-model",
        prompt_version="old-prompt",
        idempotency_key=f"related-rescreen:{role.id}:{application.id}:{label}",
    )
    db.add(row)
    return row


def _world(db, *, actionable_status: str = "processing"):
    org = Organization(
        name="Related role rescreen org",
        slug=f"related-role-rescreen-{id(db)}",
    )
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=int(org.id),
        name="ATS transport role",
        source="workable",
        workable_job_id=f"RESCREEN-{org.id}",
        workable_job_data={"state": "closed"},
        job_spec_text="Owner-only specification that must never drive the related role.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(org.id),
        name="Independent AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Independent role specification for production AI and Python systems.",
    )
    sibling = Role(
        organization_id=int(org.id),
        name="Independent Data Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Independent role specification for data platform engineering.",
    )
    db.add_all([related, sibling])
    db.flush()
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"related-rescreen-{org.id}@example.test",
        full_name="Ground Truth Candidate",
        cv_text="Python, production AI, distributed systems, and platform delivery.",
    )
    db.add(candidate)
    db.flush()
    source = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="manual",
        cv_text=candidate.cv_text,
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        # Related membership, not the evidence row lifecycle, is authoritative.
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.flush()
    related_eval = SisterRoleEvaluation(
        organization_id=int(org.id),
        role_id=int(related.id),
        candidate_id=int(candidate.id),
        source_application_id=int(source.id),
        ats_application_id=int(source.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="old-related-spec",
        cv_fingerprint="old-related-cv",
        role_fit_score=88,
        summary="Old related-role summary",
        details={
            "engine_version": "1.16.0",
            "prompt_version": "cv_match_v16",
            "summary": "Old related-role summary",
        },
        model_version="old-model",
        prompt_version="old-prompt",
        scored_at=datetime.now(timezone.utc),
    )
    sibling_eval = SisterRoleEvaluation(
        organization_id=int(org.id),
        role_id=int(sibling.id),
        candidate_id=int(candidate.id),
        source_application_id=int(source.id),
        ats_application_id=int(source.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="sibling-spec",
        cv_fingerprint="sibling-cv",
        role_fit_score=64,
        summary="Sibling role result",
        details={"engine_version": "2.1.0", "prompt_version": "holistic_v2"},
    )
    db.add_all([related_eval, sibling_eval])
    db.flush()
    related_actionable = _decision(
        db,
        role=related,
        application=source,
        status=actionable_status,
        label=actionable_status,
    )
    related_resolved = _decision(
        db,
        role=related,
        application=source,
        status="approved",
        label="approved",
    )
    sibling_pending = _decision(
        db,
        role=sibling,
        application=source,
        status="pending",
        label="sibling",
    )
    related_assessment = Assessment(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(related.id),
        application_id=int(source.id),
        token=f"related-rescreen-{org.id}",
        status=AssessmentStatus.PENDING,
        is_voided=False,
    )
    sibling_assessment = Assessment(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(sibling.id),
        application_id=int(source.id),
        token=f"sibling-rescreen-{org.id}",
        status=AssessmentStatus.PENDING,
        is_voided=False,
    )
    db.add_all([related_assessment, sibling_assessment])
    db.commit()
    return {
        "org": org,
        "owner": owner,
        "related": related,
        "sibling": sibling,
        "source": source,
        "related_eval": related_eval,
        "sibling_eval": sibling_eval,
        "related_actionable": related_actionable,
        "related_resolved": related_resolved,
        "sibling_pending": sibling_pending,
        "related_assessment": related_assessment,
        "sibling_assessment": sibling_assessment,
    }


@pytest.mark.parametrize(
    "actionable_status",
    ["pending", "processing", "reverted_for_feedback"],
)
def test_related_rescreen_uses_membership_and_isolates_role_state(
    db,
    actionable_status,
):
    world = _world(db, actionable_status=actionable_status)
    dispatch_transactions: list[bool] = []

    def _dispatch(_db, *, evaluation_id):
        dispatch_transactions.append(bool(_db.in_transaction()))
        assert evaluation_id == int(world["related_eval"].id)
        return {"status": "queued", "evaluation_id": evaluation_id}

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        side_effect=_dispatch,
    ):
        outcome = rescreen_related_role_candidates(
            db,
            world["related"],
            reason="ground_truth_related_role_rescreen",
            application_ids=[int(world["source"].id)],
        )

    assert dispatch_transactions == [False]
    assert outcome.reset_count == 1
    assert outcome.queued_count == 1
    assert outcome.decisions_invalidated == 1
    assert outcome.unscorable_count == 0

    db.refresh(world["related_eval"])
    db.refresh(world["sibling_eval"])
    assert world["related_eval"].status == "pending"
    assert world["related_eval"].role_fit_score is None
    assert world["related_eval"].details is None
    assert world["related_eval"].history[-1]["role_fit_score"] == 88
    assert world["sibling_eval"].status == "done"
    assert world["sibling_eval"].role_fit_score == 64

    db.refresh(world["related_actionable"])
    assert world["related_actionable"].status == "discarded"
    assert (
        "role-local score refresh required"
        in world["related_actionable"].resolution_note
    )
    db.refresh(world["related_resolved"])
    db.refresh(world["sibling_pending"])
    assert world["related_resolved"].status == "approved"
    assert world["sibling_pending"].status == "pending"

    # A score refresh does not destroy a valid assessment attempt. Lifecycle
    # restart callers have an explicit opt-in tested separately below.
    db.refresh(world["related_assessment"])
    db.refresh(world["sibling_assessment"])
    assert world["related_assessment"].is_voided is False
    assert world["sibling_assessment"].is_voided is False


def test_ownerless_related_role_rescreen_uses_local_membership(db):
    world = _world(db)
    world["related"].ats_owner_role_id = None
    world["related_eval"].ats_application_id = None
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ) as dispatch:
        outcome = rescreen_related_role_candidates(
            db,
            world["related"],
            reason="ownerless_ground_truth_rescreen",
            application_ids=[int(world["source"].id)],
        )

    assert outcome.matched_count == 1
    assert outcome.reset_count == 1
    assert outcome.queued_count == 1
    dispatch.assert_called_once_with(
        db,
        evaluation_id=int(world["related_eval"].id),
    )


def test_related_rescreen_can_void_only_acting_role_assessment(db):
    world = _world(db)
    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ):
        outcome = rescreen_related_role_candidates(
            db,
            world["related"],
            reason="candidate_membership_restarted",
            application_ids=[int(world["source"].id)],
            void_active_assessments=True,
        )

    assert outcome.assessments_voided == 1
    db.refresh(world["related_assessment"])
    db.refresh(world["sibling_assessment"])
    assert world["related_assessment"].is_voided is True
    assert world["sibling_assessment"].is_voided is False


def test_related_rescore_rechecks_current_engine_under_lock(db, monkeypatch):
    world = _world(db)
    from app.services import cv_score_orchestrator

    monkeypatch.setattr(cv_score_orchestrator.settings, "HOLISTIC_SCORING_ENABLED", True)
    monkeypatch.setattr(
        cv_score_orchestrator.settings,
        "HOLISTIC_SCORING_ORG_IDS",
        str(world["org"].id),
    )
    world["related_eval"].details = {
        "engine_version": "2.1.0",
        "prompt_version": "holistic_v2",
    }
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation"
    ) as dispatch:
        outcome = rescreen_related_role_candidates(
            db,
            world["related"],
            reason="agent_chat:old_engine_rescore",
            application_ids=[int(world["source"].id)],
            only_outdated=True,
        )

    assert outcome.reset_count == 0
    assert outcome.skipped_current_count == 1
    assert outcome.queued_count == 0
    dispatch.assert_not_called()
    db.refresh(world["related_eval"])
    assert world["related_eval"].role_fit_score == 88


def test_related_rescreen_reports_unscorable_reset_without_claiming_work_started(db):
    world = _world(db)
    world["source"].cv_text = None
    world["source"].candidate.cv_text = None
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation"
    ) as dispatch:
        outcome = rescreen_related_role_candidates(
            db,
            world["related"],
            reason="ground_truth_missing_evidence",
            application_ids=[int(world["source"].id)],
        )

    assert outcome.reset_count == 1
    assert outcome.unscorable_count == 1
    assert outcome.queued_count == 0
    assert outcome.waiting_count == 0
    dispatch.assert_not_called()


def test_constraints_and_rescore_share_role_local_service(monkeypatch):
    role = Role(
        id=321,
        organization_id=45,
        name="Independent related role",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=123,
    )
    service_result = RelatedRoleRescreenResult(
        role_id=321,
        requested_count=None,
        matched_count=3,
        reset_count=3,
        queued_count=2,
        waiting_count=0,
        unscorable_count=1,
        skipped_resolved_count=0,
        skipped_current_count=0,
        missing_membership_count=0,
        decisions_invalidated=2,
        assessments_voided=0,
        evaluation_ids=(1, 2, 3),
    )
    calls: list[dict] = []

    def _shared_service(_db, _role, **kwargs):
        calls.append(kwargs)
        return service_result

    monkeypatch.setattr(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates",
        _shared_service,
    )
    constraint_result = constraints.rescreen_role(
        object(),
        role,
        reason="constraint_changed",
    )
    assert constraint_result["rescreening_count"] == 2
    assert constraint_result["invalidated_count"] == 3
    assert constraint_result["queued_count"] == 2

    stale = [
        {
            "application_id": 91,
            "score": 82.0,
            "engine_version": "1.16.0",
            "_app": object(),
            "_evaluation": object(),
        }
    ]
    monkeypatch.setattr(rescore, "find_stale_scored", lambda _db, _role: stale)
    rescore_result = rescore.rescore_candidates(
        object(),
        role,
        scope="all",
        confirm=True,
    )
    assert rescore_result["rescoring_count"] == 2
    assert rescore_result["invalidated_count"] == 3
    assert rescore_result["queued_count"] == 2
    assert [call["reason"] for call in calls] == [
        "constraint_changed",
        "agent_chat:old_engine_rescore",
    ]
    assert calls[1]["application_ids"] == [91]
    assert calls[1]["only_outdated"] is True
