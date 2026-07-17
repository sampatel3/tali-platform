"""Durable current and append-only history receipts for ATS notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from .ats_note_provider import AtsNoteProviderPlan
from .document_service import sanitize_json_for_storage

ATS_NOTE_WRITEBACK_KEY = "ats_note_writeback"
ATS_NOTE_WRITEBACK_HISTORY_KEY = "ats_note_writeback_history"
UNRESOLVED_NOTE_STATUSES = frozenset(
    {"provider_call_started", "provider_succeeded", "manual_reconciliation_required"}
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(?P<key>
        authorization|access[_-]?token|refresh[_-]?token|client[_-]?secret|
        api[_-]?key|bhresttoken|corptoken|password|private[_-]?key|secret
    )
    (?P<separator>\s*["']?\s*[:=]\s*["']?)
    (?P<value>(?:bearer\s+)?[^\s,;&"'}]+)
    """
)
_BEARER_CREDENTIAL_RE = re.compile(r"\bbearer\s+[^\s,;\"'}]+", flags=re.IGNORECASE)


def note_receipt_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def note_receipt(app: CandidateApplication) -> dict[str, Any] | None:
    state = app.integration_sync_state
    raw = state.get(ATS_NOTE_WRITEBACK_KEY) if isinstance(state, dict) else None
    return dict(raw) if isinstance(raw, dict) else None


def write_note_receipt(app: CandidateApplication, receipt: dict[str, Any]) -> None:
    state = dict(app.integration_sync_state or {})
    state[ATS_NOTE_WRITEBACK_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)


def note_receipt_history(app: CandidateApplication) -> list[dict[str, Any]]:
    state = app.integration_sync_state
    raw = state.get(ATS_NOTE_WRITEBACK_HISTORY_KEY) if isinstance(state, dict) else None
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def archive_note_receipt(app: CandidateApplication, receipt: dict[str, Any]) -> None:
    state = dict(app.integration_sync_state or {})
    history = note_receipt_history(app)
    history.append(dict(receipt))
    state[ATS_NOTE_WRITEBACK_HISTORY_KEY] = history
    app.integration_sync_state = sanitize_json_for_storage(state)


def note_receipt_scope_matches(
    receipt: dict[str, Any], plan: AtsNoteProviderPlan
) -> bool:
    return bool(
        str(receipt.get("operation_id") or "") == plan.operation_id
        and int(receipt.get("application_id") or 0) == plan.application_id
        and str(receipt.get("provider") or "") == plan.provider
        and str(receipt.get("provider_target_id") or "") == plan.provider_target_id
        and str(receipt.get("application_provider_target_id") or "")
        == plan.application_provider_target_id
        and str(receipt.get("body_sha256") or "") == plan.body_sha256
        and str(receipt.get("scope_fingerprint") or "") == plan.scope_fingerprint
    )


def note_receipt_matches(receipt: dict[str, Any], plan: AtsNoteProviderPlan) -> bool:
    return bool(
        note_receipt_scope_matches(receipt, plan)
        and str(receipt.get("connection_authority_fingerprint") or "")
        == plan.connection_authority_fingerprint
        and str(receipt.get("snapshot_fingerprint") or "") == plan.snapshot_fingerprint
    )


def note_body_preview(body: str) -> str:
    preview = " ".join(str(body or "").split())
    preview = _SECRET_ASSIGNMENT_RE.sub(r"\g<key>\g<separator>[REDACTED]", preview)
    preview = _BEARER_CREDENTIAL_RE.sub("Bearer [REDACTED]", preview)
    return preview[:200]


def archive_orphaned_note_result(
    app: CandidateApplication,
    *,
    plan: AtsNoteProviderPlan,
    provider_called: bool | None,
    provider_succeeded: bool | None,
    failure_code: str | None = None,
) -> None:
    current = note_receipt(app)
    base = (
        dict(current)
        if current is not None and note_receipt_matches(current, plan)
        else {
            "operation_id": plan.operation_id,
            "application_id": plan.application_id,
            "provider": plan.provider,
            "provider_target_id": plan.provider_target_id,
            "application_provider_target_id": plan.application_provider_target_id,
            "body_sha256": plan.body_sha256,
            "body_preview": note_body_preview(plan.body),
            "scope_fingerprint": plan.scope_fingerprint,
            "connection_authority_fingerprint": (plan.connection_authority_fingerprint),
            "snapshot_fingerprint": plan.snapshot_fingerprint,
            "attempts": 1,
        }
    )
    uncertain = provider_succeeded is not False
    base.update(
        status="manual_reconciliation_required" if uncertain else "failed",
        provider_called=provider_called,
        provider_succeeded=provider_succeeded,
        provider_outcome_uncertain=provider_succeeded is None,
        manual_reconciliation_required=uncertain,
        failure_code=failure_code,
        updated_at=note_receipt_now(),
    )
    archive_note_receipt(app, base)


__all__ = [
    "ATS_NOTE_WRITEBACK_HISTORY_KEY",
    "ATS_NOTE_WRITEBACK_KEY",
    "UNRESOLVED_NOTE_STATUSES",
    "archive_note_receipt",
    "archive_orphaned_note_result",
    "note_body_preview",
    "note_receipt",
    "note_receipt_history",
    "note_receipt_matches",
    "note_receipt_scope_matches",
    "note_receipt_now",
    "write_note_receipt",
]
