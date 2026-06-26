"""``application_notes`` — recruiter notes attached to a single application.

The per-candidate analogue of :mod:`app.agent_runtime.role_feedback_notes`.
A recruiter drops a freeform note on a candidate ("already interviewed
elsewhere — not suitable", "lacks the technical depth for this role"); it

- shows in the candidate report's Notes & timeline tab, and
- when flagged ``for_agent`` (the default), rides in the ``get_application``
  payload so the recruiting agent reads it as standing per-candidate
  guidance on the next cycle — the same way role feedback notes ride in
  the system prompt.

No new table: notes are stored as ``recruiter_note`` rows in
``candidate_application_events``. That table is already read by the Notes
tab, so a note works whether or not an assessment is linked (the legacy
note path wrote to ``assessment.timeline``, which dead-ends when no
assessment exists).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.user import User

RECRUITER_NOTE_EVENT = "recruiter_note"

# Cap on what the agent sees per cycle. Older notes stay visible to the
# recruiter in the timeline UI; they just stop riding in the payload, so a
# candidate with a long note history keeps token usage bounded. Mirrors the
# role_feedback_notes caps so the two surfaces behave alike.
AGENT_VISIBLE_NOTE_LIMIT = 10
AGENT_VISIBLE_NOTE_BODY_CHARS = 600


def _actor_name(author: User | None) -> str:
    if author is None:
        return "Recruiter"
    return (getattr(author, "full_name", None) or getattr(author, "email", None) or "Recruiter")


def create_recruiter_note(
    db: Session,
    *,
    app: CandidateApplication,
    note: str,
    author: User | None = None,
    for_agent: bool = True,
    now: datetime | None = None,
) -> CandidateApplicationEvent:
    """Append a recruiter note to the application's event timeline.

    Does not commit — the caller owns the transaction. Raises ``ValueError``
    on an empty note so the route can return a 400.
    """
    cleaned = (note or "").strip()
    if not cleaned:
        raise ValueError("note is required")
    row = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        event_type=RECRUITER_NOTE_EVENT,
        actor_type="recruiter",
        actor_id=int(getattr(author, "id", 0) or 0) or None,
        reason=cleaned,
        event_metadata={
            "note": cleaned,
            "actor_name": _actor_name(author),
            "for_agent": bool(for_agent),
        },
        created_at=now or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def list_recruiter_notes(
    db: Session, *, application_id: int, limit: int = 100
) -> list[CandidateApplicationEvent]:
    """Recruiter notes for an application, newest first."""
    return (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
        )
        .order_by(
            CandidateApplicationEvent.created_at.desc(),
            CandidateApplicationEvent.id.desc(),
        )
        .limit(int(limit))
        .all()
    )


def recruiter_notes_for_agent(app: CandidateApplication) -> list[dict[str, Any]]:
    """The agent-visible slice of recruiter notes for ``get_application``.

    Reads the already-loaded ``app.events`` relationship (no extra query in
    the common path), keeps only notes flagged ``for_agent``, newest first,
    capped at :data:`AGENT_VISIBLE_NOTE_LIMIT` and truncated to
    :data:`AGENT_VISIBLE_NOTE_BODY_CHARS`. Returns a compact dict the agent
    reads as standing guidance about this candidate.
    """
    events = list(getattr(app, "events", None) or [])
    notes: list[dict[str, Any]] = []
    for event in events:
        if str(getattr(event, "event_type", "")) != RECRUITER_NOTE_EVENT:
            continue
        meta = getattr(event, "event_metadata", None) or {}
        if meta.get("for_agent") is False:
            continue
        body = str(meta.get("note") or getattr(event, "reason", "") or "").strip()
        if not body:
            continue
        if len(body) > AGENT_VISIBLE_NOTE_BODY_CHARS:
            body = body[:AGENT_VISIBLE_NOTE_BODY_CHARS] + "…"
        created_at = getattr(event, "created_at", None)
        notes.append(
            {
                "note": body,
                "author": str(meta.get("actor_name") or "Recruiter"),
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    # ``app.events`` has no guaranteed order; sort newest-first by timestamp.
    notes.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return notes[:AGENT_VISIBLE_NOTE_LIMIT]


__all__ = [
    "AGENT_VISIBLE_NOTE_BODY_CHARS",
    "AGENT_VISIBLE_NOTE_LIMIT",
    "RECRUITER_NOTE_EVENT",
    "create_recruiter_note",
    "list_recruiter_notes",
    "recruiter_notes_for_agent",
]
