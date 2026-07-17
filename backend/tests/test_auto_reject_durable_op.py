from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.components.integrations.bullhorn.provider import BullhornProvider
from app.domains.assessments_runtime.pipeline_service import transition_outcome
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.services import auto_reject_op
from app.services.auto_reject_op import (
    execute_auto_reject_op,
    surface_auto_reject_failure,
)
from app.services.auto_reject_operation_receipt import (
    AUTO_REJECT_OPERATION_KEY,
    authorize_auto_reject_operation,
    fence_auto_reject_lifecycle_restore,
    mark_auto_reject_provider_call_started,
)
from app.services.workable_actions_service import WorkableWritebackError
from app.tasks.automation_tasks import run_application_auto_reject
from tests.conftest import TestingSessionLocal


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


def _apply_concurrent_drift(application_id: int, mutation: str) -> str:
    """Commit one recruiter/authority mutation from an independent session."""

    with TestingSessionLocal() as concurrent:
        current = concurrent.get(CandidateApplication, int(application_id))
        assert current is not None
        if mutation in {"hired", "withdrawn"}:
            transition_outcome(
                concurrent,
                app=current,
                to_outcome=mutation,
                actor_type="recruiter",
                reason=f"Concurrent recruiter {mutation}",
            )
        elif mutation == "reopened":
            transition_outcome(
                concurrent,
                app=current,
                to_outcome="rejected",
                actor_type="recruiter",
                reason="Concurrent recruiter close",
            )
            transition_outcome(
                concurrent,
                app=current,
                to_outcome="open",
                actor_type="recruiter",
                reason="Concurrent recruiter reopen",
            )
        elif mutation == "application_version":
            current.version = int(current.version or 1) + 1
        elif mutation == "role_authority":
            role = concurrent.get(Role, int(current.role_id))
            assert role is not None
            role.auto_reject = False
            role.version = int(role.version or 1) + 1
        elif mutation == "workspace_authority":
            organization = concurrent.get(
                Organization, int(current.organization_id)
            )
            assert organization is not None
            organization.agent_workspace_control_version = int(
                organization.agent_workspace_control_version or 1
            ) + 1
        elif mutation == "provider_target":
            current.workable_candidate_id = (
                f"{current.workable_candidate_id}-relinked"
            )
        else:  # pragma: no cover - test helper contract
            raise AssertionError(f"unknown drift mutation: {mutation}")
        concurrent.commit()
        return str(current.application_outcome or "open")


_CONCURRENT_DRIFT_CASES = (
    ("hired", "hired"),
    ("withdrawn", "withdrawn"),
    ("reopened", "open"),
    ("application_version", "open"),
    ("role_authority", "open"),
    ("workspace_authority", "open"),
    ("provider_target", "open"),
)
_POST_PROVIDER_DRIFT_CASES = _CONCURRENT_DRIFT_CASES[3:]
_OUTCOME_DRIFT_CASES = _CONCURRENT_DRIFT_CASES[:3]


def test_lifecycle_restore_fences_pre_provider_receipt_and_bumps_version(db):
    org, role, application = _seed_application(db)
    application.deleted_at = datetime.now(timezone.utc)
    starting_version = int(application.version or 1)
    operation_id = f"auto-reject:{application.id}:old-lifecycle"
    authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "workable",
            "provider_target_id": application.workable_candidate_id,
        },
        receipt_key=operation_id,
    )
    assert fence_auto_reject_lifecycle_restore(
        db,
        application,
        actor_type="candidate",
        target_outcome="open",
    ) is True

    receipt = application.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert application.version == starting_version + 1
    assert receipt["status"] == "superseded"
    assert receipt["observed_application_version"] == starting_version + 1
    assert receipt["superseded_by_actor_type"] == "candidate"


def test_lifecycle_restore_blocks_an_ambiguous_provider_call(db):
    from app.services.application_lifecycle_restore import LifecycleRestoreDeferred

    org, role, application = _seed_application(db)
    application.deleted_at = datetime.now(timezone.utc)
    decision = authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "workable",
            "provider_target_id": application.workable_candidate_id,
        },
        receipt_key=f"auto-reject:{application.id}:in-flight",
    )
    mark_auto_reject_provider_call_started(
        application, operation_id=str(decision["operation_id"])
    )
    # Crossing the provider-call boundary is durable before network I/O; model
    # that committed state before a later restore transaction observes it.
    db.commit()

    with pytest.raises(LifecycleRestoreDeferred):
        fence_auto_reject_lifecycle_restore(
            db, application, actor_type="sync", target_outcome="open"
        )
    db.rollback()
    db.refresh(application)
    assert application.deleted_at is not None
    assert application.integration_sync_state[AUTO_REJECT_OPERATION_KEY]["status"] == (
        "provider_call_started"
    )


def test_deferred_workable_auto_reject_finalizes_once(db):
    org, _role, application = _seed_application(db)
    receipt = f"auto-reject:{application.id}:stable-receipt"
    provider_result = {"success": True, "action": "disqualify", "code": "ok"}

    def provider_call(**_kwargs):
        assert not db.in_transaction()
        return provider_result

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            side_effect=provider_call,
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
    completed_receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert completed_receipt["status"] == "completed"
    assert completed_receipt["provider_called"] is True
    assert completed_receipt["provider_succeeded"] is True
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
        patch(
            "app.services.ats_outcome_provider.perform_stage_move_provider_call",
            return_value={
                "success": True,
                "code": "ok",
                "provider": "bullhorn",
                "provider_remote_stage": "Rejected by Taali",
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


def test_bullhorn_failure_never_finalizes_a_local_reject(db):
    org, _role, application = _seed_application(db, provider="bullhorn")

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
                "success": False,
                "action": "move",
                "code": "needs_mapping",
                "message": "No rejected Bullhorn status is mapped",
            },
        ),
    ):
        with pytest.raises(WorkableWritebackError) as raised:
            execute_auto_reject_op(
                db,
                int(org.id),
                {
                    "application_id": int(application.id),
                    "actor_type": "auto",
                    "receipt_key": f"auto-reject:{application.id}:bullhorn-fail",
                },
            )

    assert raised.value.code == "needs_mapping"
    assert raised.value.retriable is False
    db.rollback()
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "open"
    assert _event_count(db, int(application.id), "bullhorn_rejected") == 0
    assert _event_count(db, int(application.id), "auto_rejected") == 0


@pytest.mark.parametrize(("mutation", "expected_outcome"), _CONCURRENT_DRIFT_CASES)
def test_pre_provider_drift_cancels_without_ats_call(
    db,
    mutation: str,
    expected_outcome: str,
):
    org, _role, application = _seed_application(db)
    application_id = int(application.id)
    original_lock = auto_reject_op.lock_auto_reject_context
    lock_calls = 0

    def _lock_after_drift(*args, **kwargs):
        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 2:
            _apply_concurrent_drift(application_id, mutation)
        return original_lock(*args, **kwargs)

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch.object(
            auto_reject_op,
            "lock_auto_reject_context",
            side_effect=_lock_after_drift,
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable"
        ) as provider,
    ):
        result = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": application_id,
                "actor_type": "auto",
                "receipt_key": f"auto-reject:{application_id}:pre:{mutation}",
            },
        )

    assert result["status"] == "skipped"
    assert result["performed"] is False
    assert result["provider_performed"] is False
    provider.assert_not_called()
    db.expire_all()
    persisted = db.get(CandidateApplication, application_id)
    assert persisted is not None
    assert persisted.application_outcome == expected_outcome
    receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert receipt["status"] in {"cancelled_before_provider", "superseded"}
    assert receipt["provider_called"] is False
    assert _event_count(
        db, application_id, "auto_reject_writeback_cancelled"
    ) == 1
    assert _event_count(db, application_id, "auto_rejected") == 0


@pytest.mark.parametrize(("mutation", "expected_outcome"), _POST_PROVIDER_DRIFT_CASES)
def test_provider_success_never_overwrites_concurrent_local_authority(
    db,
    mutation: str,
    expected_outcome: str,
):
    org, _role, application = _seed_application(db)
    application_id = int(application.id)

    def _provider_call(**_kwargs):
        _apply_concurrent_drift(application_id, mutation)
        return {"success": True, "action": "disqualify", "code": "ok"}

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            side_effect=_provider_call,
        ) as provider,
    ):
        result = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": application_id,
                "actor_type": "auto",
                "receipt_key": f"auto-reject:{application_id}:post:{mutation}",
            },
        )

    assert result["status"] == "manual_reconciliation_required"
    assert result["performed"] is False
    assert result["provider_performed"] is True
    provider.assert_called_once()
    db.expire_all()
    persisted = db.get(CandidateApplication, application_id)
    assert persisted is not None
    assert persisted.application_outcome == expected_outcome
    assert persisted.auto_reject_state == "manual_reconciliation_required"
    receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert receipt["status"] == "manual_reconciliation_required"
    assert receipt["provider_called"] is True
    assert receipt["provider_succeeded"] is True
    assert receipt["observed_application_outcome"] == expected_outcome
    assert _event_count(
        db, application_id, "auto_reject_manual_reconciliation_required"
    ) == 1
    assert _event_count(db, application_id, "auto_rejected") == 0
    assert _event_count(db, application_id, "workable_auto_reject_applied") == 0


@pytest.mark.parametrize(("mutation", "_expected_outcome"), _OUTCOME_DRIFT_CASES)
def test_provider_started_receipt_blocks_competing_outcome_mutation(
    db, mutation: str, _expected_outcome: str
):
    org, _role, application = _seed_application(db)
    application_id = int(application.id)

    def _provider_call(**_kwargs):
        with pytest.raises(HTTPException) as blocked:
            _apply_concurrent_drift(application_id, mutation)
        assert blocked.value.status_code == 409
        return {"success": True, "action": "disqualify", "code": "ok"}

    with (
        patch(
            "app.services.application_automation_service.evaluate_auto_reject_decision",
            return_value=dict(_BELOW_THRESHOLD),
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            side_effect=_provider_call,
        ) as provider,
    ):
        result = execute_auto_reject_op(
            db,
            int(org.id),
            {
                "application_id": application_id,
                "actor_type": "auto",
                "receipt_key": f"auto-reject:{application_id}:blocked:{mutation}",
            },
        )

    assert result["status"] == "ok"
    assert result["performed"] is True
    provider.assert_called_once()
    db.expire_all()
    persisted = db.get(CandidateApplication, application_id)
    assert persisted.application_outcome == "rejected"
    assert persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]["status"] == (
        "completed"
    )
    assert _event_count(db, application_id, "auto_rejected") == 1


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
    authorized = authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=_role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "workable",
            "provider_target_id": application.workable_candidate_id,
        },
        receipt_key=receipt,
    )
    mark_auto_reject_provider_call_started(
        application,
        operation_id=str(authorized["operation_id"]),
    )
    db.commit()
    error = WorkableWritebackError(
        action="auto_reject",
        code="api_error",
        message="Provider outcome was not confirmed",
        retriable=True,
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
    assert _event_count(
        db,
        int(application.id),
        "auto_reject_manual_reconciliation_required",
    ) == 1
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
        )
        .count()
        == 0
    )
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "open"
    assert persisted.auto_reject_state == "manual_reconciliation_required"
    assert "could not be confirmed" in (persisted.auto_reject_reason or "")
    failed_receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert failed_receipt["status"] == "failed"
    assert failed_receipt["provider_called"] is None
    assert failed_receipt["provider_succeeded"] is None
    assert failed_receipt["provider_outcome_uncertain"] is True
    assert failed_receipt["manual_reconciliation_required"] is True


def test_definite_pre_provider_failure_returns_reject_to_hitl(db):
    org, role, application = _seed_application(db)
    receipt = f"auto-reject:{application.id}:pre-provider-failure"
    authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "workable",
            "provider_target_id": application.workable_candidate_id,
        },
        receipt_key=receipt,
    )
    db.commit()

    with patch(
        "app.services.auto_reject_deferred.evaluate_auto_reject_decision",
        return_value=dict(_BELOW_THRESHOLD),
    ):
        surface_auto_reject_failure(
            db,
            organization_id=int(org.id),
            payload={
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
            error=WorkableWritebackError(
                action="auto_reject",
                code="missing_write_scope",
                message="Workable token is missing write scope",
                retriable=False,
            ),
        )

    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.auto_reject_state == "awaiting_recruiter_approval"
    failed_receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
    assert failed_receipt["status"] == "failed"
    assert failed_receipt["provider_called"] is False
    assert failed_receipt["provider_succeeded"] is False
    assert failed_receipt["provider_outcome_uncertain"] is False
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
        )
        .count()
        == 1
    )


def test_terminal_failure_uses_receipt_provider_for_mixed_id_application(db):
    org, role, application = _seed_application(db, provider="bullhorn")
    application.workable_candidate_id = "legacy-workable-id"
    receipt = f"auto-reject:{application.id}:bullhorn-mixed-id"
    authorized = authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "bullhorn",
            "provider_target_id": application.bullhorn_job_submission_id,
        },
        receipt_key=receipt,
    )
    mark_auto_reject_provider_call_started(
        application,
        operation_id=str(authorized["operation_id"]),
    )
    db.commit()

    with patch(
        "app.services.auto_reject_deferred.evaluate_auto_reject_decision",
        return_value=dict(_BELOW_THRESHOLD),
    ):
        surface_auto_reject_failure(
            db,
            organization_id=int(org.id),
            payload={
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
            error=WorkableWritebackError(
                action="auto_reject",
                code="api_error",
                message="Bullhorn outcome was not confirmed",
                retriable=True,
            ),
        )

    assert _event_count(db, int(application.id), "bullhorn_writeback_failed") == 1
    assert _event_count(db, int(application.id), "workable_writeback_failed") == 0
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.auto_reject_state == "manual_reconciliation_required"


def test_superseded_terminal_failure_cannot_mutate_restored_lifecycle(db):
    org, role, application = _seed_application(db)
    receipt = f"auto-reject:{application.id}:superseded-failure"
    authorize_auto_reject_operation(
        app=application,
        organization=org,
        role=role,
        decision={
            **_BELOW_THRESHOLD,
            "provider": "workable",
            "provider_target_id": application.workable_candidate_id,
        },
        receipt_key=receipt,
    )
    application.deleted_at = datetime.now(timezone.utc)
    fence_auto_reject_lifecycle_restore(
        db,
        application,
        actor_type="candidate",
        target_outcome="open",
    )
    application.deleted_at = None
    db.commit()

    with patch(
        "app.services.auto_reject_deferred.evaluate_auto_reject_decision",
        return_value=dict(_BELOW_THRESHOLD),
    ) as evaluate:
        surface_auto_reject_failure(
            db,
            organization_id=int(org.id),
            payload={
                "application_id": int(application.id),
                "actor_type": "auto",
                "receipt_key": receipt,
            },
            error=WorkableWritebackError(
                action="auto_reject",
                code="api_error",
                message="Stale provider failure",
                retriable=True,
            ),
        )

    evaluate.assert_not_called()
    db.expire_all()
    persisted = db.get(CandidateApplication, int(application.id))
    assert persisted is not None
    assert persisted.application_outcome == "open"
    assert persisted.auto_reject_state is None
    assert (
        persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]["status"]
        == "superseded"
    )
    assert _event_count(db, int(application.id), "auto_reject_failed") == 0
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(application.id))
        .count()
        == 0
    )


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
