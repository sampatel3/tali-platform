"""Indexed, non-secret completion evidence for exact ATS notes."""

from __future__ import annotations

import hashlib
from typing import Literal

from sqlalchemy.orm import Session

from ..models.candidate_application_event import CandidateApplicationEvent
from .ats_note_provider import AtsNoteProviderPlan


def ats_note_event_key(
    operation_id: str,
    outcome: str,
    *,
    attempt: int | None = None,
) -> str:
    """Return a collision-resistant application-event idempotency key."""

    suffix = f":attempt:{max(1, int(attempt or 1))}" if attempt is not None else ""
    token = f"ats-note:{operation_id}:{outcome}{suffix}"
    if len(token) <= 200:
        return token
    digest = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()
    return f"ats-note:{digest}:{outcome}{suffix}"[:200]


def confirmed_note_event_status(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    operation_id: str,
    note_intent_sha256: str,
) -> Literal["missing", "exact", "mismatch"]:
    """Check durable completion before consulting mutable provider authority."""

    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == int(organization_id),
            CandidateApplicationEvent.application_id == int(application_id),
            CandidateApplicationEvent.idempotency_key
            == ats_note_event_key(operation_id, "confirmed"),
        )
        .one_or_none()
    )
    if event is None:
        return "missing"
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    if (
        str(metadata.get("operation_id") or "") == str(operation_id)
        and str(metadata.get("note_intent_sha256") or "")
        == str(note_intent_sha256)
    ):
        return "exact"
    return "mismatch"


def confirmed_note_metadata(
    plan: AtsNoteProviderPlan,
    *,
    note_intent_sha256: str,
) -> dict[str, object]:
    """Return the exact non-secret evidence stored on a confirmed event."""

    return {
        "operation_id": plan.operation_id,
        "note_intent_sha256": str(note_intent_sha256),
        "ats_provider": plan.provider,
        "provider_target_id": plan.provider_target_id,
        "application_provider_target_id": plan.application_provider_target_id,
        "body_sha256": plan.body_sha256,
        "scope_fingerprint": plan.scope_fingerprint,
        "snapshot_fingerprint": plan.snapshot_fingerprint,
        "provider_called": True,
    }


__all__ = [
    "ats_note_event_key",
    "confirmed_note_event_status",
    "confirmed_note_metadata",
]
