"""``role_feedback_notes`` — read/write recruiter feedback notes for a role.

Tiny module — the model is append-only, the agent reads the recent
slice. Lives next to ``role_intent`` because the sub-agents consume
both at score time via ``system_prompt``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.role_feedback_note import RoleFeedbackNote


# Cap on what the agent sees per cycle. Notes older than the most-recent
# N are still visible to the recruiter in the timeline UI; they just
# stop riding in the system prompt. Keeps token usage bounded for a
# role with a long feedback history.
AGENT_VISIBLE_NOTE_LIMIT = 10
AGENT_VISIBLE_NOTE_BODY_CHARS = 600


def create_note(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    note: str,
    author_user_id: int | None = None,
    now: datetime | None = None,
) -> RoleFeedbackNote:
    cleaned = (note or "").strip()
    if not cleaned:
        raise ValueError("note is required")
    row = RoleFeedbackNote(
        organization_id=organization_id,
        role_id=role_id,
        author_user_id=author_user_id,
        note=cleaned,
        created_at=now or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def list_notes(
    db: Session, *, role_id: int, limit: int = 100
) -> list[RoleFeedbackNote]:
    return (
        db.query(RoleFeedbackNote)
        .filter(RoleFeedbackNote.role_id == role_id)
        .order_by(RoleFeedbackNote.created_at.desc(), RoleFeedbackNote.id.desc())
        .limit(int(limit))
        .all()
    )


def list_for_agent(db: Session, *, role_id: int) -> list[RoleFeedbackNote]:
    return list_notes(db, role_id=role_id, limit=AGENT_VISIBLE_NOTE_LIMIT)


__all__ = [
    "AGENT_VISIBLE_NOTE_BODY_CHARS",
    "AGENT_VISIBLE_NOTE_LIMIT",
    "create_note",
    "list_for_agent",
    "list_notes",
]
