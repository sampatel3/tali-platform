from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.components.integrations.bullhorn.provider import BullhornProvider
from app.services.auto_reject_op import (
    execute_auto_reject_op,
    surface_auto_reject_failure,
)
from app.services.workable_actions_service import WorkableWritebackError
from app.tasks.automation_tasks import run_application_auto_reject


_BELOW_THRESHOLD = {
    "should_trigger": True,
    "state": "eligible",
    "reason": "Below pre-screen threshold",
    "auto_disqualify_eligible": True,
    "config": {
        "threshold_100": 50,
        "workable_actor_member_id": "member-1",
        "workable_disqualify_reason_id": None,
        "auto_reject_note_template": None,
    },
    "snapshot": {
        "pre_screen_score": 10,
        "cv_fit_score": None,
        "requirements_fit_score": None,
    },
}


def _seed_application(db, *, paused: bool = False, provider: str = "workable"):
    suffix = uuid4().hex
    org = Organization(
        name=f"Durable auto reject {suffix}",
        slug=f"durable-auto-reject-{suffix}",
        workable_connected=provider == "workable",
        workable_access_token=("token" if provider == "workable" else None),
        workable_subdomain=("workspace" if provider == "workable" else None),
        workable_config={
            "workable_writeback": True,
            "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        },
        bullhorn_connected=provider == "bullhorn",
        bullhorn_client_id=("client-id" if provider == "bullhorn" else None),
        bullhorn_client_secret=("client-secret" if provider == "bullhorn" else None),
        bullhorn_refresh_token=("refresh-token" if provider == "bullhorn" else None),
        bullhorn_username=("api-user" if provider == "bullhorn" else None),
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        source="manual",
        agentic_mode_enabled=True,
        agent_paused_at=(datetime.now(timezone.utc) if paused else None),
        auto_reject=True,
        score_threshold=50,
        monthly_usd_budget_cents=0,
        job_spec_text="Build reliable Python services.",
    )
    candidate = Candidate(
        organization_id=int(org.id),
        full_name="Durable Candidate",
        email=f"durable-{suffix}@example.test",
    )
    db.add_all([role, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        source="manual",
        workable_candidate_id=(
            f"workable-{suffix}" if provider == "workable" else None
        ),
        bullhorn_job_submission_id=(
            f"submission-{suffix}" if provider == "bullhorn" else None
        ),
        genuine_pre_screen_score_100=10,
        pre_screen_score_100=10,
        pre_screen_recommendation="Below threshold",
        pre_screen_run_at=datetime.now(timezone.utc),
    )
    db.add(application)
    db.commit()
    return org, role, application


def _event_count(db, application_id: int, event_type: str) -> int:
    return (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(application_id),
            CandidateApplicationEvent.event_type == event_type,
        )
        .count()
    )


def test_deferred_workable_auto_reject_finalizes_once(db):
    org, _role, application = _seed_application(db)
    receipt = f"auto-reject:{application.id}:stable-receipt"
    provider_result = {"success": True, "action": "disqualify", "code": "ok"}

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            return_value=provider_result,
        ) as provider,
    ):
        first = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
        )
        duplicate = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
        )

    assert first["status"] == "ok"
    assert first["performed"] is True
    assert duplicate["status"] == "skipped"
    assert duplicate["reason"] == "application_closed"
    provider.assert_called_once()
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "rejected"
    assert persisted.auto_reject_state == "rejected"
    for event_type in (
        "auto_reject_writeback_started",
        "workable_disqualified",
        "auto_rejected",
        "workable_auto_reject_applied",
    ):
        assert _event_count(db, int(application.id), event_type) == 1


def test_deferred_bullhorn_auto_reject_reconciles_remote_status(db):
    org, _role, application = _seed_application(db, provider="bullhorn")
    receipt = f"auto-reject:{application.id}:bullhorn-receipt"

    with (
        patch("app.platform.config.settings.BULLHORN_ENABLED", True),
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch.object(
            BullhornProvider,
            "reject_application",
            return_value={
                "success": True,
                "action": "move",
                "code": "ok",
                "config": {"remote_status": "Rejected by Taali"},
            },
        ) as provider,
    ):
        result = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
        )

    assert result["status"] == "ok"
    assert result["provider"] == "bullhorn"
    provider.assert_called_once()
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "rejected"
    assert persisted.bullhorn_status == "Rejected by Taali"
    assert persisted.external_stage_normalized == "rejected"
    assert _event_count(db, int(application.id), "bullhorn_rejected") == 1
    assert _event_count(db, int(application.id), "auto_rejected") == 1


def test_paused_role_cards_live_auto_reject_without_provider_call(db):
    org, _role, application = _seed_application(db, paused=True)

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable"
        ) as provider,
    ):
        result = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": f"auto-reject:{application.id}:paused",
            },
        )

    assert result["status"] == "ok"
    assert result["performed"] is False
    assert result["state"] == "awaiting_recruiter_approval"
    assert result["reason"] == "role agent is paused"
    provider.assert_not_called()
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "open"
    assert persisted.auto_reject_state == "awaiting_recruiter_approval"
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
        )
        .count()
        == 1
    )


def test_terminal_auto_reject_failure_is_visible_and_idempotent(db):
    org, _role, application = _seed_application(db)
    receipt = f"auto-reject:{application.id}:terminal-failure"
    error = WorkableWritebackError(
        action="auto_reject",
        code="not_writeable",
        message="Provider rejected the update",
        retriable=False,
    )

    with patch(
        "app.services.auto_reject_deferred.evaluate_auto_reject_decision",
        return_value=dict(_BELOW_THRESHOLD),
    ):
        for _ in range(2):
            surface_auto_reject_failure(
                db,
                organization_id=int(org.id),
                payload={
                    "application_id": int(application.id),
                    "actor_type": "auto",
                    "receipt_key": receipt,
                },
                error=error,
            )

    assert _event_count(db, int(application.id), "workable_writeback_failed") == 1
    assert _event_count(db, int(application.id), "auto_reject_failed") == 1
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
        )
        .count()
        == 1
    )
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "open"
    assert persisted.auto_reject_state == "awaiting_recruiter_approval"


def test_auto_reject_dispatch_receipt_tracks_exact_input_snapshot(db):
    _org, role, application = _seed_application(db)

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=91
    ) as enqueue:
        first = run_application_auto_reject.run(int(application.id))
        second = run_application_auto_reject.run(int(application.id))
        first_call = enqueue.call_args_list[0].kwargs
        second_call = enqueue.call_args_list[1].kwargs

        db.expire_all()
        current_role = db.get(Role, int(role.id))
        assert current_role is not None
        current_role.version = int(current_role.version or 1) + 1
        db.commit()
        third = run_application_auto_reject.run(int(application.id))
        third_call = enqueue.call_args_list[2].kwargs

    assert first["status"] == second["status"] == third["status"] == "queued"
    assert first["receipt_key"] == second["receipt_key"]
    assert first_call["dispatch_key"] == second_call["dispatch_key"]
    assert third["receipt_key"] != first["receipt_key"]
    assert third_call["dispatch_key"] != first_call["dispatch_key"]
    assert set(first_call["payload"]) == {
        "application_id",
        "actor_type",
        "receipt_key",
    }
    assert first_call["payload"]["receipt_key"] == first_call["dispatch_key"]
