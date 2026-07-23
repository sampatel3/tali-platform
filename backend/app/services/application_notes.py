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
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.user import User

if TYPE_CHECKING:
    from ..models.application_interview import ApplicationInterview

RECRUITER_NOTE_EVENT = "recruiter_note"

# Cap on what the agent sees per cycle. Older notes stay visible to the
# recruiter in the timeline UI; they just stop riding in the payload, so a
# candidate with a long note history keeps token usage bounded. Mirrors the
# role_feedback_notes caps so the two surfaces behave alike.
AGENT_VISIBLE_NOTE_LIMIT = 10
AGENT_VISIBLE_NOTE_BODY_CHARS = 600

# Body cap for the auto-generated interview transcript note. Generous enough
# for a Fireflies short-summary, but bounded so a pasted transcript summary
# can't balloon the timeline.
TRANSCRIPT_NOTE_BODY_CHARS = 900

# How a stored ``stage`` reads in the note headline.
_INTERVIEW_STAGE_LABELS = {
    "screening": "Screening call",
    "tech_stage_2": "Technical interview",
}


def _actor_name(author: User | None) -> str:
    if author is None:
        return "Recruiter"
    return (getattr(author, "full_name", None) or getattr(author, "email", None) or "Recruiter")


def _agent_readable_note(meta: dict[str, Any]) -> str:
    """Render the agent-visible body for a note, structured-kind aware.

    Plain notes read as-is. A ``ranking`` note prefixes the recruiter's score
    ("Ranking: 4/5 — solid but light on X"); a ``link`` note renders the label
    + URL ("Link: Portfolio — https://…"). This is what rides in the
    ``recruiter_notes`` payload the agent reads as standing guidance.
    """
    body = str(meta.get("note") or "").strip()
    kind = str(meta.get("kind") or "note").strip().lower()
    if kind == "ranking":
        ranking = meta.get("ranking")
        prefix = f"Ranking: {ranking}/5" if ranking is not None else "Ranking"
        return f"{prefix} — {body}" if body else prefix
    if kind == "link":
        url = str(meta.get("link_url") or "").strip()
        label = str(meta.get("link_label") or "").strip()
        parts = [p for p in (label, url) if p]
        rendered = " ".join(parts) if parts else body
        return f"Link: {rendered}".strip()
    return body


def create_recruiter_note(
    db: Session,
    *,
    app: CandidateApplication,
    note: str,
    author: User | None = None,
    for_agent: bool = True,
    kind: str = "note",
    ranking: int | None = None,
    link_url: str | None = None,
    link_label: str | None = None,
    role_id: int | None = None,
    now: datetime | None = None,
) -> CandidateApplicationEvent:
    """Append a recruiter note to the application's event timeline.

    Does not commit — the caller owns the transaction. Raises ``ValueError``
    on an empty note (for the freeform/ranking kinds) or a missing URL (for
    the ``link`` kind) so the route can return a 400.

    ``kind`` selects the flavour stored in metadata: ``note`` (freeform),
    ``ranking`` (1–5 + optional comment), or ``link`` (URL + optional label).
    The structured bits ride in ``event_metadata`` so the FE can differentiate
    them and :func:`recruiter_notes_for_agent` can read a readable form.
    """
    kind = (kind or "note").strip().lower()
    if kind not in ("note", "ranking", "link"):
        kind = "note"
    cleaned = (note or "").strip()
    cleaned_url = (link_url or "").strip()
    cleaned_label = (link_label or "").strip()
    # A link note is allowed to have an empty comment (the URL is the payload);
    # everything else requires a non-empty note body.
    if kind == "link":
        if not cleaned_url:
            raise ValueError("link_url is required")
    elif not cleaned:
        raise ValueError("note is required")

    meta: dict[str, Any] = {
        "note": cleaned,
        "actor_name": _actor_name(author),
        "for_agent": bool(for_agent),
        "kind": kind,
    }
    if kind == "ranking" and ranking is not None:
        meta["ranking"] = int(ranking)
    if kind == "link":
        meta["link_url"] = cleaned_url
        if cleaned_label:
            meta["link_label"] = cleaned_label

    # ``reason`` mirrors the agent-readable body so existing readers (the audit
    # timeline, the events list, the org-scoping test that checks ``reason``)
    # keep showing something sensible for the structured kinds too.
    logical_role_id = int(role_id) if role_id is not None else int(app.role_id)
    row = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        role_id=logical_role_id,
        event_type=RECRUITER_NOTE_EVENT,
        actor_type="recruiter",
        actor_id=int(getattr(author, "id", 0) or 0) or None,
        reason=_agent_readable_note(meta) or cleaned,
        event_metadata={**meta, "acting_role_id": logical_role_id},
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
    logical_role_id = int(getattr(app, "role_id", 0) or 0)
    notes: list[dict[str, Any]] = []
    for event in events:
        if str(getattr(event, "event_type", "")) != RECRUITER_NOTE_EVENT:
            continue
        event_role_id = int(getattr(event, "role_id", 0) or 0)
        if logical_role_id and event_role_id != logical_role_id:
            continue
        meta = getattr(event, "event_metadata", None) or {}
        if meta.get("for_agent") is False:
            continue
        # Structured kinds (ranking / link) render a readable prefix so the
        # agent reads "Ranking: 4/5 — …" / "Link: <label> <url>" rather than a
        # bare comment. Plain notes fall back to the note body / reason.
        body = (_agent_readable_note(meta) or str(getattr(event, "reason", "") or "")).strip()
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


def _interview_stage_label(stage: str | None) -> str:
    return _INTERVIEW_STAGE_LABELS.get(str(stage or "").strip().lower(), "Interview")


def create_interview_transcript_note(
    db: Session,
    *,
    app: CandidateApplication,
    interview: "ApplicationInterview",
    author: User | None = None,
    source_label: str | None = None,
    now: datetime | None = None,
) -> CandidateApplicationEvent | None:
    """Drop a timeline note summarising a just-linked interview transcript.

    Fired once per interview from each ingest path — manual paste, manual
    Fireflies link, and webhook auto-match. The note lands in the candidate's
    Notes & timeline and, being ``for_agent``, rides in the ``get_application``
    payload as standing guidance for Stage-2 follow-ups. ``transcript_url`` in
    the metadata gives the FE a "View full transcript" link back to Fireflies.

    Idempotent per interview: a redelivered webhook (which upserts the same
    interview row) or a re-link won't spawn a duplicate note. Returns the new
    event, or ``None`` when a note for this interview already exists. Does not
    commit — the caller owns the transaction.
    """
    interview_id = int(getattr(interview, "id", 0) or 0)
    # Dedup: skip if this interview already carries a transcript note. The
    # scan is bounded (an application rarely holds hundreds of notes) and
    # avoids JSON-path filters that diverge across SQLite/Postgres.
    if interview_id:
        for existing in list_recruiter_notes(db, application_id=app.id, limit=200):
            meta = getattr(existing, "event_metadata", None) or {}
            if int(meta.get("interview_transcript_id") or 0) == interview_id:
                return None

    stage_label = _interview_stage_label(getattr(interview, "stage", None))
    summary = str(getattr(interview, "summary", None) or "").strip()
    if not summary:
        transcript_text = str(getattr(interview, "transcript_text", None) or "").strip()
        summary = (transcript_text[:280].rstrip() + "…") if len(transcript_text) > 280 else transcript_text
    meeting_date = getattr(interview, "meeting_date", None)
    date_suffix = f" ({meeting_date.date().isoformat()})" if isinstance(meeting_date, datetime) else ""

    headline = f"{stage_label} transcript attached{date_suffix}."
    body = f"{headline} {summary}".strip() if summary else headline
    if len(body) > TRANSCRIPT_NOTE_BODY_CHARS:
        body = body[:TRANSCRIPT_NOTE_BODY_CHARS].rstrip() + "…"

    transcript_url = str(getattr(interview, "provider_url", None) or "").strip()

    meta: dict[str, Any] = {
        "note": body,
        "actor_name": source_label or _actor_name(author),
        "for_agent": True,
        "kind": "note",
        "interview_transcript_id": interview_id or None,
        "interview_stage": str(getattr(interview, "stage", None) or "") or None,
        "interview_source": str(getattr(interview, "source", None) or "") or None,
    }
    if transcript_url:
        meta["transcript_url"] = transcript_url

    row = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        role_id=int(app.role_id),
        event_type=RECRUITER_NOTE_EVENT,
        actor_type="recruiter",
        actor_id=int(getattr(author, "id", 0) or 0) or None,
        reason=body,
        event_metadata=meta,
        created_at=now or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "AGENT_VISIBLE_NOTE_BODY_CHARS",
    "AGENT_VISIBLE_NOTE_LIMIT",
    "RECRUITER_NOTE_EVENT",
    "TRANSCRIPT_NOTE_BODY_CHARS",
    "create_interview_transcript_note",
    "create_recruiter_note",
    "list_recruiter_notes",
    "recruiter_notes_for_agent",
]
