"""Durable, provider-neutral receipts for asynchronous ATS outcome writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .document_service import sanitize_json_for_storage

if TYPE_CHECKING:
    from ..models.candidate_application import CandidateApplication


OUTCOME_WRITEBACK_KEY = "outcome_writeback"
OUTCOME_WRITEBACK_STATUSES = frozenset({"queued", "confirmed", "failed"})


def set_outcome_writeback_state(
    app: "CandidateApplication",
    *,
    provider: str,
    status: str,
    target_outcome: str,
    job_run_id: int | None = None,
    error_code: str | None = None,
    remote_status: str | None = None,
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
    previous = sync_state.get(OUTCOME_WRITEBACK_KEY)
    previous = dict(previous) if isinstance(previous, dict) else {}
    receipt: dict[str, Any] = {
        "provider": str(provider or "ats").strip().lower() or "ats",
        "status": normalized_status,
        "target_outcome": str(target_outcome or "").strip().lower(),
        "requested_at": previous.get("requested_at") or now,
        "updated_at": now,
    }
    if job_run_id is not None:
        receipt["job_run_id"] = int(job_run_id)
    elif previous.get("job_run_id") is not None:
        receipt["job_run_id"] = previous["job_run_id"]
    if error_code:
        receipt["error_code"] = str(error_code)[:100]
    if remote_status:
        receipt["remote_status"] = str(remote_status)[:200]
    if normalized_status == "confirmed":
        receipt["confirmed_at"] = now
    elif normalized_status == "failed":
        receipt["failed_at"] = now

    sync_state[OUTCOME_WRITEBACK_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(sync_state)
    return receipt


def replace_sync_state_preserving_writeback(
    app: "CandidateApplication", state: dict[str, Any]
) -> None:
    """Replace provider sync metadata while retaining an in-flight receipt."""

    previous = (
        app.integration_sync_state
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    writeback = previous.get(OUTCOME_WRITEBACK_KEY)
    merged = dict(state)
    if isinstance(writeback, dict):
        merged[OUTCOME_WRITEBACK_KEY] = dict(writeback)
    app.integration_sync_state = sanitize_json_for_storage(merged)


__all__ = [
    "OUTCOME_WRITEBACK_KEY",
    "replace_sync_state_preserving_writeback",
    "set_outcome_writeback_state",
]
