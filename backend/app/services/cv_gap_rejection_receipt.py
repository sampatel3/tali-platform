"""Durable per-application fence for recruiter-confirmed CV-gap rejection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..models.candidate_application import CandidateApplication
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage


CV_GAP_REJECTION_OPERATION_KEY = "cv_gap_rejection_operation"
ACTIVE_CV_GAP_RECEIPT_STATUSES = frozenset(
    {"authorized", "provider_call_started", "provider_succeeded"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_state(app: CandidateApplication) -> dict[str, Any]:
    return (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )


def cv_gap_rejection_receipt(app: CandidateApplication) -> dict[str, Any] | None:
    value = _sync_state(app).get(CV_GAP_REJECTION_OPERATION_KEY)
    return dict(value) if isinstance(value, dict) else None


def _write_receipt(
    app: CandidateApplication,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    state = _sync_state(app)
    state[CV_GAP_REJECTION_OPERATION_KEY] = dict(receipt)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt


def authorize_cv_gap_rejection(
    app: CandidateApplication,
    *,
    operation_id: str,
    needs_input_id: int,
    kind: str,
    owner_role_id: int,
    expected_owner_role_version: int,
    provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist the exact app/provider snapshot approved for one ATS effect."""

    existing = cv_gap_rejection_receipt(app)
    # Never let a duplicate batch replace another operation while its provider
    # outcome is unresolved. The caller's exact-operation drift check will
    # classify the later batch without reaching provider I/O.
    if existing is not None and str(
        existing.get("status") or ""
    ) in ACTIVE_CV_GAP_RECEIPT_STATUSES:
        return existing
    now = _now()
    receipt = {
        "operation_id": str(operation_id),
        "status": "authorized",
        "application_id": int(app.id),
        "expected_application_outcome": str(
            app.application_outcome or "open"
        ).strip().lower(),
        "expected_application_version": int(app.version or 1),
        "role_id": int(app.role_id),
        "owner_role_id": int(owner_role_id),
        "expected_owner_role_version": int(expected_owner_role_version),
        "needs_input_id": int(needs_input_id),
        "kind": str(kind),
        "provider": str(provider_snapshot.get("provider") or "local"),
        "provider_target_id": str(provider_snapshot.get("provider_target_id") or ""),
        "provider_write_required": bool(provider_snapshot.get("write_required")),
        "provider_called": False,
        "provider_succeeded": False,
        "provider_outcome_uncertain": False,
        "authorized_at": now,
        "updated_at": now,
    }
    return _write_receipt(app, receipt)


def cv_gap_receipt_drift_reason(
    app: CandidateApplication | None,
    *,
    operation_id: str,
    owner_role_id: int,
    kind: str,
    provider_snapshot: dict[str, Any] | None,
) -> str | None:
    if app is None:
        return "application_unavailable"
    receipt = cv_gap_rejection_receipt(app)
    if receipt is None:
        return "operation_receipt_missing"
    if str(receipt.get("operation_id") or "") != str(operation_id):
        return "operation_receipt_replaced"
    if str(receipt.get("status") or "") not in ACTIVE_CV_GAP_RECEIPT_STATUSES:
        return f"operation_{str(receipt.get('status') or 'inactive')}"
    if app.deleted_at is not None:
        return "application_deleted"
    if int(app.role_id) != int(owner_role_id):
        return "application_role_changed"
    if str(app.application_outcome or "open").strip().lower() != str(
        receipt.get("expected_application_outcome") or "open"
    ):
        return "application_outcome_changed"
    if int(app.version or 1) != int(receipt.get("expected_application_version") or 0):
        return "application_version_changed"
    if str(app.cv_text or "").strip():
        return "cv_text_became_available"
    has_file = bool(str(app.cv_file_url or "").strip())
    if kind == "missing_cv" and has_file:
        return "cv_file_became_available"
    if kind == "cv_unreadable" and not has_file:
        return "cv_file_no_longer_present"
    if provider_snapshot is not None:
        if str(provider_snapshot.get("provider") or "local") != str(
            receipt.get("provider") or "local"
        ):
            return "ats_provider_changed"
        if str(provider_snapshot.get("provider_target_id") or "") != str(
            receipt.get("provider_target_id") or ""
        ):
            return "provider_target_changed"
        if bool(provider_snapshot.get("write_required")) != bool(
            receipt.get("provider_write_required")
        ):
            return "provider_write_requirement_changed"
    return None


def mark_cv_gap_provider_call_started(
    app: CandidateApplication,
    *,
    operation_id: str,
) -> None:
    receipt = cv_gap_rejection_receipt(app)
    if (
        receipt is None
        or str(receipt.get("operation_id") or "") != str(operation_id)
        or str(receipt.get("status") or "") != "authorized"
    ):
        return
    now = _now()
    receipt.update(
        status="provider_call_started",
        provider_call_started_at=now,
        provider_called=None,
        provider_succeeded=None,
        provider_outcome_uncertain=True,
        updated_at=now,
    )
    _write_receipt(app, receipt)


def mark_cv_gap_provider_succeeded(
    app: CandidateApplication,
    *,
    operation_id: str,
    provider_result: dict[str, Any],
) -> None:
    receipt = cv_gap_rejection_receipt(app)
    if (
        receipt is None
        or str(receipt.get("operation_id") or "") != str(operation_id)
        or str(receipt.get("status") or "")
        not in {"provider_call_started", "provider_succeeded"}
    ):
        return
    now = _now()
    safe_result = {
        "provider": str(receipt.get("provider") or "local"),
        "provider_target_id": str(receipt.get("provider_target_id") or ""),
        "write_required": bool(receipt.get("provider_write_required")),
        "success": True,
        "code": sanitize_text_for_storage(str(provider_result.get("code") or "")),
    }
    receipt.update(
        status="provider_succeeded",
        provider_succeeded_at=now,
        provider_called=bool(receipt.get("provider_write_required")),
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        provider_result_code=sanitize_text_for_storage(
            str(provider_result.get("code") or "")
        ),
        provider_result=safe_result,
        updated_at=now,
    )
    _write_receipt(app, receipt)


def provider_result_from_cv_gap_receipt(
    receipt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Rebuild the safe finalization input from exact durable success proof."""

    if receipt is None or str(receipt.get("status") or "") != "provider_succeeded":
        return None
    stored = receipt.get("provider_result")
    stored = dict(stored) if isinstance(stored, dict) else {}
    return {
        "provider": str(stored.get("provider") or receipt.get("provider") or "local"),
        "provider_target_id": str(
            stored.get("provider_target_id")
            or receipt.get("provider_target_id")
            or ""
        ),
        "write_required": bool(
            stored.get("write_required", receipt.get("provider_write_required"))
        ),
        "success": True,
        "code": sanitize_text_for_storage(
            str(stored.get("code") or receipt.get("provider_result_code") or "")
        ),
    }


def complete_cv_gap_rejection(
    app: CandidateApplication,
    *,
    operation_id: str,
) -> None:
    receipt = cv_gap_rejection_receipt(app)
    if receipt is None or str(receipt.get("operation_id") or "") != str(operation_id):
        return
    now = _now()
    receipt.update(
        status="completed",
        completed_at=now,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_application_version=int(app.version or 1),
        updated_at=now,
    )
    _write_receipt(app, receipt)


def fail_cv_gap_rejection(
    app: CandidateApplication,
    *,
    operation_id: str,
    reason: str,
    provider_called: bool | None,
) -> None:
    receipt = cv_gap_rejection_receipt(app)
    if receipt is None or str(receipt.get("operation_id") or "") != str(operation_id):
        return
    now = _now()
    receipt.update(
        status="failed",
        failed_at=now,
        failure_reason=sanitize_text_for_storage(reason),
        provider_called=provider_called,
        provider_succeeded=False if provider_called is False else None,
        provider_outcome_uncertain=provider_called is None,
        manual_reconciliation_required=provider_called is None,
        updated_at=now,
    )
    _write_receipt(app, receipt)


def surface_cv_gap_manual_reconciliation(
    db: Session,
    *,
    app: CandidateApplication,
    operation_id: str,
    provider: str,
    reason: str,
    actor_id: int | None,
    provider_succeeded: bool | None,
) -> None:
    """Persist an explicit, idempotent trail for an ambiguous remote outcome."""

    receipt = cv_gap_rejection_receipt(app) or {}
    if str(receipt.get("operation_id") or "") == str(operation_id):
        now = _now()
        receipt.update(
            status="manual_reconciliation_required",
            reconciliation_reason=sanitize_text_for_storage(reason),
            provider_called=True,
            provider_succeeded=provider_succeeded,
            provider_outcome_uncertain=provider_succeeded is None,
            manual_reconciliation_required=True,
            observed_application_outcome=str(app.application_outcome or "open"),
            observed_application_version=int(app.version or 1),
            updated_at=now,
        )
        _write_receipt(app, receipt)
    append_application_event(
        db,
        app=app,
        event_type="cv_gap_rejection_manual_reconciliation_required",
        actor_type="recruiter",
        actor_id=actor_id,
        reason=reason,
        metadata={
            "operation_id": operation_id,
            "ats_provider": provider,
            "provider_succeeded": provider_succeeded,
            "local_outcome_preserved": str(app.application_outcome or "open"),
        },
        idempotency_key=f"{operation_id}:reconcile"[:200],
    )


__all__ = [
    "ACTIVE_CV_GAP_RECEIPT_STATUSES",
    "authorize_cv_gap_rejection",
    "complete_cv_gap_rejection",
    "cv_gap_receipt_drift_reason",
    "cv_gap_rejection_receipt",
    "fail_cv_gap_rejection",
    "mark_cv_gap_provider_call_started",
    "mark_cv_gap_provider_succeeded",
    "provider_result_from_cv_gap_receipt",
    "surface_cv_gap_manual_reconciliation",
]
