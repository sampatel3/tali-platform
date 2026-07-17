from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.pipeline_service import transition_outcome
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.application_lifecycle_restore import (
    LifecycleRestoreDeferred,
    fence_application_lifecycle_restore,
)
from app.services.ats_writeback_state import (
    OUTCOME_WRITEBACK_KEY,
    OUTCOME_WRITEBACK_RECONCILIATION_KEY,
    set_outcome_writeback_state,
)
from app.services.manual_outcome_lifecycle import (
    finalize_manual_outcome_success,
    preflight_manual_outcome,
    surface_manual_outcome_failure,
)
from app.services.workable_op_runner import OP_MANUAL_OUTCOME, execute_op
from tests.conftest import TestingSessionLocal


def _seed(db, *, provider: str = "workable"):
    org = Organization(name=f"Manual outcome {provider}", slug=f"manual-outcome-{provider}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Engineer", source=provider)
    candidate = Candidate(
        organization_id=org.id,
        email=f"manual-{provider}-{id(db)}@example.test",
        full_name="Manual Candidate",
    )
    db.add_all([role, candidate])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="rejected",
        pipeline_stage="review",
        application_outcome="rejected",
        version=3,
        source=provider,
        workable_candidate_id=("workable-1" if provider == "workable" else None),
        bullhorn_job_submission_id=("bullhorn-1" if provider == "bullhorn" else None),
    )
    db.add(app)
    db.flush()
    operation_id = f"manual-outcome:{org.id}:{app.id}:exact"
    provider_target_id = (
        app.workable_candidate_id
        if provider == "workable"
        else app.bullhorn_job_submission_id
    )
    set_outcome_writeback_state(
        app,
        provider=provider,
        status="queued",
        target_outcome="rejected",
        expected_application_version=3,
        expected_local_outcome="rejected",
        operation_id=operation_id,
        provider_target_id=str(provider_target_id),
    )
    db.commit()
    payload = {
        "application_id": int(app.id),
        "target_outcome": "rejected",
        "expected_application_version": 3,
        "expected_local_outcome": "rejected",
        "operation_id": operation_id,
        "provider": provider,
        "provider_target_id": str(provider_target_id),
        "reason": "Not a match",
    }
    return org, role, app, payload


def test_current_workable_operation_claims_then_confirms(db):
    org, _role, app, payload = _seed(db)

    def provider(**_kwargs):
        assert not db.in_transaction()
        return {"success": True, "code": "ok"}

    with (
        patch("app.services.workable_op_runner._route_bullhorn_op", return_value=None),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            side_effect=provider,
        ) as disqualify,
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_MANUAL_OUTCOME,
            payload=payload,
        )

    assert result["status"] == "ok"
    disqualify.assert_called_once()
    db.refresh(app)
    receipt = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert receipt["status"] == "confirmed"
    assert receipt["operation_id"] == payload["operation_id"]
    assert receipt["provider_succeeded"] is True


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
@pytest.mark.parametrize(
    ("mutation", "outcome"),
    [("soft_delete", "rejected"), ("restore", "rejected"), ("reapply", "open")],
)
def test_stale_lifecycle_never_reaches_either_provider(db, provider, mutation, outcome):
    org, _role, app, payload = _seed(db, provider=provider)
    if mutation == "soft_delete":
        app.deleted_at = datetime.now(timezone.utc)
    else:
        app.version = 4
        app.application_outcome = outcome
    db.commit()

    with (
        patch("app.services.workable_op_runner._route_bullhorn_op") as route,
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable"
        ) as disqualify,
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_MANUAL_OUTCOME,
            payload=payload,
        )

    assert result["status"] == "superseded"
    route.assert_not_called()
    disqualify.assert_not_called()
    db.refresh(app)
    assert app.integration_sync_state[OUTCOME_WRITEBACK_KEY]["status"] == "superseded"


def test_inflight_a_blocks_outcome_b_without_overwriting_receipt(db):
    org, _role, app, payload = _seed(db)
    assert preflight_manual_outcome(db, int(org.id), payload) is None

    with pytest.raises(HTTPException) as exc_info:
        transition_outcome(
            db,
            app=app,
            to_outcome="open",
            actor_type="recruiter",
            reason="Changed mind",
        )

    assert exc_info.value.status_code == 409
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "rejected"
    receipt = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert receipt["operation_id"] == payload["operation_id"]
    assert receipt["status"] == "provider_call_started"


def test_late_success_preserves_newer_receipt_and_surfaces_orphan(db):
    _org, _role, app, payload_a = _seed(db)
    app.application_outcome = "open"
    app.version = 4
    operation_b = f"manual-outcome:{app.organization_id}:{app.id}:newer"
    set_outcome_writeback_state(
        app,
        provider="workable",
        status="queued",
        target_outcome="open",
        expected_application_version=4,
        expected_local_outcome="open",
        operation_id=operation_b,
        provider_target_id=str(app.workable_candidate_id),
    )
    db.commit()

    result = finalize_manual_outcome_success(
        db, app, payload_a, provider="workable"
    )

    assert result and result["status"] == "manual_reconciliation_required"
    db.refresh(app)
    primary = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert primary["operation_id"] == operation_b
    assert primary["status"] == "queued"
    orphan = app.integration_sync_state[OUTCOME_WRITEBACK_RECONCILIATION_KEY]
    assert orphan["operation_id"] == payload_a["operation_id"]
    assert orphan["provider_succeeded"] is True


def test_post_provider_success_reloads_concurrent_stage_version(db):
    org, _role, app, payload = _seed(db)
    assert preflight_manual_outcome(db, int(org.id), payload) is None
    concurrent = TestingSessionLocal()
    try:
        current = concurrent.get(CandidateApplication, int(app.id))
        current.pipeline_stage = "advanced"
        current.version = 4
        concurrent.commit()
    finally:
        concurrent.close()

    result = finalize_manual_outcome_success(
        db, app, payload, provider="workable"
    )

    assert result and result["status"] == "manual_reconciliation_required"
    db.refresh(app)
    assert app.pipeline_stage == "advanced"
    assert app.version == 4
    receipt = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert receipt["operation_id"] == payload["operation_id"]
    assert receipt["status"] == "manual_reconciliation_required"
    assert receipt["provider_succeeded"] is True


def test_late_bullhorn_success_never_stamps_a_drifted_application(db):
    org, _role, app, payload = _seed(db, provider="bullhorn")
    assert preflight_manual_outcome(db, int(org.id), payload) is None
    concurrent = TestingSessionLocal()
    try:
        current = concurrent.get(CandidateApplication, int(app.id))
        current.version = 4
        current.pipeline_stage = "advanced"
        current.bullhorn_status = "Newer remote status"
        current.external_stage_raw = "Newer remote status"
        current.external_stage_normalized = "advanced"
        concurrent.commit()
    finally:
        concurrent.close()

    with patch(
        "app.services.ats_outcome_provider.stamp_bullhorn_outcome_success"
    ) as stamp:
        result = finalize_manual_outcome_success(
            db,
            app,
            payload,
            provider="bullhorn",
            remote_status="Rejected",
            on_exact_success=stamp,
        )

    assert result and result["status"] == "manual_reconciliation_required"
    stamp.assert_not_called()
    db.refresh(app)
    assert app.bullhorn_status == "Newer remote status"
    assert app.external_stage_raw == "Newer remote status"
    assert app.external_stage_normalized == "advanced"


@pytest.mark.parametrize("mutation", ["target", "provider"])
def test_provider_or_exact_target_drift_never_reaches_remote(db, mutation):
    org, _role, app, payload = _seed(db, provider="bullhorn")
    if mutation == "target":
        app.bullhorn_job_submission_id = "bullhorn-new-target"
    else:
        app.workable_candidate_id = "workable-now-authoritative"
    db.commit()

    with (
        patch("app.services.workable_op_runner._route_bullhorn_op") as route,
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable"
        ) as disqualify,
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_MANUAL_OUTCOME,
            payload=payload,
        )

    assert result["status"] == "superseded"
    route.assert_not_called()
    disqualify.assert_not_called()


@pytest.mark.parametrize(
    ("error_code", "status", "blocks"),
    [
        ("needs_mapping", "failed", False),
        ("api_error", "manual_reconciliation_required", True),
    ],
)
def test_drifted_failure_only_blocks_when_provider_result_is_ambiguous(
    db, error_code, status, blocks
):
    org, _role, app, payload = _seed(db)
    assert preflight_manual_outcome(db, int(org.id), payload) is None
    concurrent = TestingSessionLocal()
    try:
        current = concurrent.get(CandidateApplication, int(app.id))
        current.pipeline_stage = "advanced"
        current.version = 4
        concurrent.commit()
    finally:
        concurrent.close()

    assert surface_manual_outcome_failure(
        db,
        app,
        payload,
        error_code=error_code,
        error_message="Provider failed",
    )
    db.refresh(app)
    receipt = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert receipt["status"] == status
    assert receipt["manual_reconciliation_required"] is blocks
    app.deleted_at = datetime.now(timezone.utc)
    db.commit()
    if blocks:
        with pytest.raises(LifecycleRestoreDeferred):
            fence_application_lifecycle_restore(
                db, app, actor_type="sync", target_outcome="open"
            )
        db.rollback()
    else:
        assert fence_application_lifecycle_restore(
            db, app, actor_type="sync", target_outcome="open"
        )


@pytest.mark.parametrize(
    ("error_code", "expected_status", "uncertain"),
    [
        ("needs_mapping", "failed", False),
        ("api_error", "manual_reconciliation_required", True),
    ],
)
def test_terminal_failure_preserves_provider_phase_truth(
    db, error_code, expected_status, uncertain
):
    org, _role, app, payload = _seed(db)
    assert preflight_manual_outcome(db, int(org.id), payload) is None

    assert surface_manual_outcome_failure(
        db,
        app,
        payload,
        error_code=error_code,
        error_message="Provider failed",
    )

    db.refresh(app)
    receipt = app.integration_sync_state[OUTCOME_WRITEBACK_KEY]
    assert receipt["status"] == expected_status
    assert receipt["provider_outcome_uncertain"] is uncertain
    app.deleted_at = datetime.now(timezone.utc)
    db.commit()
    if uncertain:
        with pytest.raises(LifecycleRestoreDeferred):
            fence_application_lifecycle_restore(
                db, app, actor_type="sync", target_outcome="open"
            )
        db.rollback()
    else:
        assert fence_application_lifecycle_restore(
            db, app, actor_type="sync", target_outcome="open"
        )


def test_stale_session_reloads_started_receipt_before_outcome_change(db):
    org, _role, app, payload = _seed(db)
    app_id = int(app.id)
    stale = TestingSessionLocal()
    worker = TestingSessionLocal()
    try:
        stale_app = stale.get(CandidateApplication, app_id)
        worker_app = worker.get(CandidateApplication, app_id)
        set_outcome_writeback_state(
            worker_app,
            provider="workable",
            status="provider_call_started",
            target_outcome="rejected",
            expected_application_version=3,
            expected_local_outcome="rejected",
            operation_id=payload["operation_id"],
        )
        worker.commit()

        with pytest.raises(HTTPException):
            transition_outcome(
                stale,
                app=stale_app,
                to_outcome="open",
                actor_type="sync",
            )
        stale.rollback()
    finally:
        stale.close()
        worker.close()
    db.expire_all()
    persisted = db.get(CandidateApplication, app_id)
    assert persisted.application_outcome == "rejected"
    assert persisted.integration_sync_state[OUTCOME_WRITEBACK_KEY]["status"] == (
        "provider_call_started"
    )


def test_stale_preflight_reloads_exact_provider_target_before_claim(db):
    org, _role, app, payload = _seed(db, provider="bullhorn")
    app_id = int(app.id)
    stale = TestingSessionLocal()
    concurrent = TestingSessionLocal()
    try:
        stale_app = stale.get(CandidateApplication, app_id)
        assert stale_app.bullhorn_job_submission_id == "bullhorn-1"
        current = concurrent.get(CandidateApplication, app_id)
        current.bullhorn_job_submission_id = "bullhorn-concurrent-target"
        concurrent.commit()

        result = preflight_manual_outcome(stale, int(org.id), payload)
        assert result and result["status"] == "superseded"
    finally:
        stale.close()
        concurrent.close()
    db.expire_all()
    persisted = db.get(CandidateApplication, app_id)
    assert persisted.bullhorn_job_submission_id == "bullhorn-concurrent-target"
    assert persisted.integration_sync_state[OUTCOME_WRITEBACK_KEY]["status"] == (
        "superseded"
    )


def test_stale_session_reloads_version_and_outcome_before_preconditions(db):
    _org, _role, app, _payload = _seed(db)
    app_id = int(app.id)
    stale = TestingSessionLocal()
    concurrent = TestingSessionLocal()
    try:
        stale_app = stale.get(CandidateApplication, app_id)
        current = concurrent.get(CandidateApplication, app_id)
        current.application_outcome = "hired"
        current.version = 4
        concurrent.commit()

        with pytest.raises(HTTPException) as exc_info:
            transition_outcome(
                stale,
                app=stale_app,
                to_outcome="open",
                actor_type="sync",
                expected_version=3,
            )
        assert "Version mismatch" in str(exc_info.value.detail)
        stale.rollback()
    finally:
        stale.close()
        concurrent.close()
    db.expire_all()
    persisted = db.get(CandidateApplication, app_id)
    assert persisted.application_outcome == "hired"
    assert persisted.version == 4


@pytest.mark.parametrize(
    ("key", "receipt"),
    [
        (
            "cv_gap_rejection_operation",
            {
                "operation_id": "cv-gap:1",
                "status": "provider_succeeded",
                "provider_succeeded": True,
            },
        ),
        (
            OUTCOME_WRITEBACK_KEY,
            {
                "operation_id": "manual:ambiguous",
                "status": "failed",
                "provider_outcome_uncertain": True,
            },
        ),
    ],
)
def test_restore_blocks_post_provider_or_ambiguously_named_receipts(db, key, receipt):
    _org, _role, app, _payload = _seed(db)
    app.deleted_at = datetime.now(timezone.utc)
    app.integration_sync_state = {key: receipt}
    db.commit()

    with pytest.raises(LifecycleRestoreDeferred):
        fence_application_lifecycle_restore(
            db, app, actor_type="sync", target_outcome="open"
        )
    db.rollback()
    db.refresh(app)
    assert app.deleted_at is not None
    assert app.integration_sync_state[key]["status"] == receipt["status"]


def test_new_outcome_operation_does_not_inherit_prior_provider_lineage(db):
    _org, _role, app, _payload = _seed(db, provider="bullhorn")
    set_outcome_writeback_state(
        app,
        provider="bullhorn",
        status="failed",
        target_outcome="rejected",
        job_run_id=91,
        error_code="old-error",
        expected_application_version=3,
        expected_local_outcome="rejected",
        operation_id="bullhorn-old-operation",
        provider_target_id="bullhorn-1",
        provider_outcome_uncertain=False,
    )
    old_requested_at = app.integration_sync_state[OUTCOME_WRITEBACK_KEY][
        "requested_at"
    ]

    receipt = set_outcome_writeback_state(
        app,
        provider="workable",
        status="provider_call_started",
        target_outcome="open",
        expected_application_version=4,
        expected_local_outcome="rejected",
        operation_id="workable-new-operation",
        provider_target_id="workable-new-target",
    )

    assert receipt["provider"] == "workable"
    assert receipt["operation_id"] == "workable-new-operation"
    assert receipt["provider_target_id"] == "workable-new-target"
    assert receipt["target_outcome"] == "open"
    assert receipt["requested_at"] != old_requested_at
    assert "job_run_id" not in receipt
    assert "error_code" not in receipt
