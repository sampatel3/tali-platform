"""Durable, provider-neutral receipts for asynchronous ATS outcome writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .document_service import sanitize_json_for_storage

if TYPE_CHECKING:
    from ..models.candidate_application import CandidateApplication


OUTCOME_WRITEBACK_KEY = "outcome_writeback"
OUTCOME_WRITEBACK_RECONCILIATION_KEY = "outcome_writeback_reconciliation"
OUTCOME_WRITEBACK_STATUSES = frozenset(
    {
        "queued",
        "provider_call_started",
        "confirmed",
        "failed",
        "manual_reconciliation_required",
        "superseded",
    }
)
_DURABLE_SYNC_RECEIPT_KEYS = (
    OUTCOME_WRITEBACK_KEY,
    OUTCOME_WRITEBACK_RECONCILIATION_KEY,
    "auto_reject_operation",
    "cv_gap_rejection_operation",
    "stage_move_operation",
    "decision_provider_operation",
    "ats_note_writeback",
)
_DURABLE_SYNC_HISTORY_KEYS = (
    "stage_move_operation_history",
    "decision_provider_operation_history",
    "ats_note_writeback_history",
)
_RECONCILIATION_RECEIPT_KEYS = (
    "reconciliation_status",
    "reconciliation_resolved_at",
    "provider_reconciled_at",
    "resolved_operation_id",
    "resolved_receipt_key",
    "reconciliation_resolved_by_actor_id",
    "reconciliation_resolved_by_actor_type",
    "reconciliation_evidence",
    "reconciliation_observation_id",
    "reconciliation_disposition",
    "reconciliation_observation",
    "reconciliation_observation_history",
    "reconciliation_resolution_history",
    "reconciliation_last_checked_at",
)


def set_outcome_writeback_state(
    app: "CandidateApplication",
    *,
    provider: str,
    status: str,
    target_outcome: str,
    job_run_id: int | None = None,
    error_code: str | None = None,
    remote_status: str | None = None,
    expected_application_version: int | None = None,
    expected_local_outcome: str | None = None,
    provider_outcome_uncertain: bool | None = None,
    operation_id: str | None = None,
    provider_target_id: str | None = None,
) -> dict[str, Any]:
    """Persist an honest receipt without discarding ordinary sync metadata."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in OUTCOME_WRITEBACK_STATUSES:
        raise ValueError(f"unsupported ATS outcome writeback status: {status!r}")

    now = datetime.now(timezone.utc).isoformat()
    sync_state = (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    raw_previous = sync_state.get(OUTCOME_WRITEBACK_KEY)
    raw_previous = dict(raw_previous) if isinstance(raw_previous, dict) else {}
    normalized_provider = str(provider or "ats").strip().lower() or "ats"
    normalized_target = str(target_outcome or "").strip().lower()
    same_operation = bool(raw_previous) and (
        str(raw_previous.get("provider") or "").strip().lower()
        == normalized_provider
        and str(raw_previous.get("target_outcome") or "").strip().lower()
        == normalized_target
        and (
            operation_id is None
            or str(raw_previous.get("operation_id") or "") == str(operation_id)
        )
        and (
            provider_target_id is None
            or str(raw_previous.get("provider_target_id") or "")
            == str(provider_target_id)
        )
    )
    previous = raw_previous if same_operation else {}
    exact_previous_operation = bool(
        same_operation
        and operation_id
        and provider_target_id
        and str(raw_previous.get("operation_id") or "") == str(operation_id)
        and str(raw_previous.get("provider_target_id") or "")
        == str(provider_target_id)
    )
    receipt: dict[str, Any] = {
        "application_id": int(app.id),
        "provider": normalized_provider,
        "status": normalized_status,
        "target_outcome": normalized_target,
        "requested_at": previous.get("requested_at") or now,
        "updated_at": now,
    }
    if exact_previous_operation:
        for key in _RECONCILIATION_RECEIPT_KEYS:
            if key in previous:
                receipt[key] = previous[key]
    if job_run_id is not None:
        receipt["job_run_id"] = int(job_run_id)
    elif previous.get("job_run_id") is not None:
        receipt["job_run_id"] = previous["job_run_id"]
    if error_code:
        receipt["error_code"] = str(error_code)[:100]
    if remote_status:
        receipt["remote_status"] = str(remote_status)[:200]
    if expected_application_version is not None:
        receipt["expected_application_version"] = int(expected_application_version)
    elif previous.get("expected_application_version") is not None:
        receipt["expected_application_version"] = previous[
            "expected_application_version"
        ]
    if expected_local_outcome is not None:
        receipt["expected_local_outcome"] = (
            str(expected_local_outcome).strip().lower()
        )
    elif previous.get("expected_local_outcome") is not None:
        receipt["expected_local_outcome"] = previous["expected_local_outcome"]
    if operation_id is not None:
        receipt["operation_id"] = str(operation_id)[:200]
    elif previous.get("operation_id") is not None:
        receipt["operation_id"] = previous["operation_id"]
    if provider_target_id is not None:
        receipt["provider_target_id"] = str(provider_target_id)[:200]
    elif previous.get("provider_target_id") is not None:
        receipt["provider_target_id"] = previous["provider_target_id"]
    if normalized_status == "confirmed":
        receipt.update(
            confirmed_at=now,
            provider_called=True,
            provider_succeeded=True,
            provider_outcome_uncertain=False,
            manual_reconciliation_required=False,
        )
    elif normalized_status == "failed":
        receipt["failed_at"] = now
    elif normalized_status == "provider_call_started":
        receipt.update(
            provider_call_started_at=now,
            provider_called=None,
            provider_succeeded=None,
            provider_outcome_uncertain=True,
        )
    elif normalized_status == "manual_reconciliation_required":
        receipt.update(
            reconciliation_required_at=now,
            provider_called=None,
            provider_succeeded=None,
            provider_outcome_uncertain=True,
            manual_reconciliation_required=True,
        )
    elif normalized_status == "superseded":
        receipt["superseded_at"] = now
    if provider_outcome_uncertain is not None:
        receipt["provider_outcome_uncertain"] = provider_outcome_uncertain
        receipt["manual_reconciliation_required"] = provider_outcome_uncertain
        receipt["provider_called"] = None if provider_outcome_uncertain else False
        receipt["provider_succeeded"] = None if provider_outcome_uncertain else False

    sync_state[OUTCOME_WRITEBACK_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(sync_state)
    return receipt


def replace_sync_state_preserving_writeback(
    app: "CandidateApplication", state: dict[str, Any]
) -> None:
    """Replace provider metadata while retaining durable ATS-operation receipts."""

    previous = (
        app.integration_sync_state
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    merged = dict(state)
    for receipt_key in _DURABLE_SYNC_RECEIPT_KEYS:
        receipt = previous.get(receipt_key)
        if isinstance(receipt, dict):
            merged[receipt_key] = dict(receipt)
    for history_key in _DURABLE_SYNC_HISTORY_KEYS:
        history = previous.get(history_key)
        if isinstance(history, list):
            merged[history_key] = [
                dict(item) for item in history if isinstance(item, dict)
            ]
    app.integration_sync_state = sanitize_json_for_storage(merged)


__all__ = [
    "OUTCOME_WRITEBACK_KEY",
    "OUTCOME_WRITEBACK_RECONCILIATION_KEY",
    "replace_sync_state_preserving_writeback",
    "set_outcome_writeback_state",
]
