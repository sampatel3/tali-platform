"""Durable publisher for a recruiter's asynchronous ATS outcome write."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_writeback_state import set_outcome_writeback_state
from .manual_outcome_lifecycle import build_manual_outcome_operation_id


def failed_manual_outcome_can_rearm(
    app: CandidateApplication,
    *,
    organization_id: int,
    target_outcome: str,
    idempotency_key: str | None,
) -> bool:
    """Return whether a provably uncalled Bullhorn delivery can be republished."""

    state = app.integration_sync_state
    receipt = state.get("outcome_writeback") if isinstance(state, dict) else None
    target = str(target_outcome or "").strip().lower()
    if not isinstance(receipt, dict):
        return False
    expected_operation_id = build_manual_outcome_operation_id(
        organization_id=organization_id,
        application_id=int(app.id),
        application_version=int(app.version),
        target_outcome=target,
        idempotency_key=idempotency_key,
    )
    try:
        return bool(
            receipt.get("status") == "failed"
            and receipt.get("provider") == "bullhorn"
            and receipt.get("provider_called") is False
            and receipt.get("provider_outcome_uncertain") is False
            and receipt.get("manual_reconciliation_required") is not True
            and receipt.get("operation_id") == expected_operation_id
            and int(receipt.get("expected_application_version")) == int(app.version)
            and receipt.get("expected_local_outcome") == target
            and receipt.get("target_outcome") == target
            and str(receipt.get("provider_target_id") or "")
            == str(app.bullhorn_job_submission_id or "")
            and str(app.application_outcome or "open").strip().lower() == target
        )
    except (TypeError, ValueError):
        return False


def enqueue_manual_outcome_writeback(
    db: Session,
    *,
    app: CandidateApplication,
    organization_id: int,
    user_id: int,
    target_outcome: str,
    reason: str | None,
    idempotency_key: str | None,
) -> int:
    """Commit the local outcome, then publish one exact replay-safe operation."""

    from .workable_op_runner import OP_MANUAL_OUTCOME, enqueue_workable_op

    operation_id = build_manual_outcome_operation_id(
        organization_id=organization_id,
        application_id=int(app.id),
        application_version=int(app.version),
        target_outcome=target_outcome,
        idempotency_key=idempotency_key,
    )
    application_version = int(app.version)
    set_outcome_writeback_state(
        app,
        provider="bullhorn",
        status="queued",
        target_outcome=target_outcome,
        expected_application_version=application_version,
        expected_local_outcome=target_outcome,
        operation_id=operation_id,
        provider_target_id=str(app.bullhorn_job_submission_id or ""),
    )
    db.commit()
    db.refresh(app)
    payload = {
        "application_id": int(app.id),
        "user_id": int(user_id),
        "target_outcome": target_outcome,
        "expected_application_version": application_version,
        "expected_local_outcome": target_outcome,
        "operation_id": operation_id,
        "provider": "bullhorn",
        "provider_target_id": str(app.bullhorn_job_submission_id or ""),
        "reason": reason,
    }
    try:
        return enqueue_workable_op(
            organization_id=organization_id,
            op_type=OP_MANUAL_OUTCOME,
            payload=payload,
            dispatch_key=operation_id,
        )
    except Exception as exc:
        db.rollback()
        failed_app = db.get(CandidateApplication, int(app.id))
        if failed_app is not None:
            set_outcome_writeback_state(
                failed_app,
                provider="bullhorn",
                status="failed",
                target_outcome=target_outcome,
                error_code=f"queue_persistence_{type(exc).__name__}",
                expected_application_version=application_version,
                expected_local_outcome=target_outcome,
                provider_outcome_uncertain=False,
                operation_id=operation_id,
                provider_target_id=str(
                    failed_app.bullhorn_job_submission_id or ""
                ),
            )
            db.commit()
        raise


__all__ = [
    "enqueue_manual_outcome_writeback",
    "failed_manual_outcome_can_rearm",
]
