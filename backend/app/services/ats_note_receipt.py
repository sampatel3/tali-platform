"""Bounded current-delivery receipt for exact ATS notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from .ats_note_provider import AtsNoteProviderPlan
from .document_service import sanitize_json_for_storage

ATS_NOTE_WRITEBACK_KEY = "ats_note_writeback"
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


__all__ = [
    "ATS_NOTE_WRITEBACK_KEY",
    "UNRESOLVED_NOTE_STATUSES",
    "note_body_preview",
    "note_receipt",
    "note_receipt_matches",
    "note_receipt_scope_matches",
    "note_receipt_now",
    "write_note_receipt",
]
