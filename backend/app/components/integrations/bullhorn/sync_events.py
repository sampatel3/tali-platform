"""JobSubmissionHistory → application events, and Notes → candidate context.

Two append-only, idempotent importers keyed off a single JobSubmission's
application row:

* :func:`import_submission_history` mirrors the Workable event vocabulary —
  each JobSubmissionHistory row (a status change over time) becomes one
  ``bullhorn_status_change`` row in ``candidate_application_events``, deduped by
  an idempotency key derived from the history row id. Re-syncing the same
  history never double-writes.

* :func:`import_notes` mirrors how Workable comments feed candidate context:
  each Bullhorn Note about the candidate becomes a ``recruiter_note`` event
  (flagged ``for_agent``) via the SAME store the recruiter-notes UI + agent
  read (:mod:`app.services.application_notes`), deduped by the Note id. This is
  the exact seam that makes the note ride in ``get_application`` as standing
  per-candidate guidance for the recruiting agent.

Neither importer moves the pipeline stage or triggers scoring — they only append
history/context. Both no-op cleanly on empty input and never raise into the
sync loop.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import append_application_event
from ....models.candidate_application import CandidateApplication
from ....models.candidate_application_event import CandidateApplicationEvent
from ....services.application_notes import RECRUITER_NOTE_EVENT
from ....services.document_service import sanitize_text_for_storage
from .service import BullhornService

logger = logging.getLogger(__name__)

# Read contracts (Bullhorn returns only requested fields).
SUBMISSION_HISTORY_FIELDS = "id,status,dateAdded,modifyingUser"
NOTE_FIELDS = "id,comments,action,dateAdded,commentingPerson"

BULLHORN_STATUS_CHANGE_EVENT = "bullhorn_status_change"


def _history_idempotency_key(history_id: object) -> str:
    return f"bullhorn_jsh:{history_id}"


def _note_idempotency_key(note_id: object) -> str:
    return f"bullhorn_note:{note_id}"


def import_submission_history(
    *,
    db: Session,
    app: CandidateApplication,
    submission_id: str,
    client: BullhornService,
) -> int:
    """Append JobSubmissionHistory rows as application events. Returns count added.

    Append-only + idempotent: the history-row id anchors the idempotency key, so
    ``append_application_event`` returns the existing row on a re-sync instead of
    inserting a duplicate. The Bullhorn status string is preserved verbatim in
    ``to_stage`` (the remote status) and metadata — this is an audit trail of the
    remote pipeline, not a Taali stage transition.
    """
    try:
        history = client.get_job_submission_history(
            job_submission_id=submission_id, fields=SUBMISSION_HISTORY_FIELDS
        )
    except Exception as exc:
        logger.error(
            "Bullhorn JobSubmissionHistory read failed submission=%s error_type=%s",
            submission_id,
            type(exc).__name__,
        )
        raise

    # Chronological so the appended trail reads oldest→newest and each row's
    # ``from_stage`` can carry the prior status.
    def _sort_key(row: dict) -> tuple:
        return (row.get("dateAdded") or 0, row.get("id") or 0)

    # Bullhorn's JPQL /query over an association can return the SAME history row
    # twice (a where-join fan-out). Collapse to one row per id first: two adds with
    # the same idempotency_key in one un-flushed batch would both slip past the
    # in-transaction pre-check (session is autoflush=False) and collide on the
    # unique constraint at flush. Keep the first occurrence in sort order.
    ordered = sorted((r for r in history if isinstance(r, dict)), key=_sort_key)
    seen_ids: set = set()
    deduped: list[dict] = []
    for row in ordered:
        rid = row.get("id")
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        deduped.append(row)

    added = 0
    prev_status: str | None = None
    for row in deduped:
        status = str(row.get("status") or "").strip()
        if not status:
            continue
        key = _history_idempotency_key(row.get("id"))
        # Pre-check existence so the returned-existing-row case (idempotent
        # re-sync) isn't miscounted as a new append. ``append_application_event``
        # itself is still the idempotency backstop via the unique constraint.
        already = (
            db.query(CandidateApplicationEvent.id)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.idempotency_key == key,
            )
            .first()
            is not None
        )
        append_application_event(
            db,
            app=app,
            event_type=BULLHORN_STATUS_CHANGE_EVENT,
            actor_type="sync",
            reason=f"Bullhorn status: {status}",
            from_stage=sanitize_text_for_storage(prev_status) if prev_status else None,
            to_stage=sanitize_text_for_storage(status),
            metadata={
                "bullhorn_status": status,
                "bullhorn_job_submission_id": submission_id,
                "date_added": row.get("dateAdded"),
            },
            idempotency_key=key,
        )
        if not already:
            added += 1
        prev_status = status
    return added


def _note_already_imported(db: Session, *, application_id: int, note_id: object) -> bool:
    key = _note_idempotency_key(note_id)
    existing = (
        db.query(CandidateApplicationEvent.id)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            CandidateApplicationEvent.idempotency_key == key,
        )
        .first()
    )
    return existing is not None


def import_notes(
    *,
    db: Session,
    app: CandidateApplication,
    bullhorn_candidate_id: str,
    client: BullhornService,
    now: datetime,
) -> int:
    """Import Bullhorn Notes about the candidate as agent-visible context events.

    Each Note becomes a ``recruiter_note`` row (flagged ``for_agent``) on the
    application timeline — the same store the recruiter-notes UI writes and the
    agent reads. Idempotent on the Note id so a re-sync never duplicates. Returns
    the number of new notes imported.
    """
    try:
        notes = client.query_notes(candidate_id=bullhorn_candidate_id, fields=NOTE_FIELDS)
    except Exception as exc:
        logger.error(
            "Bullhorn Notes read failed candidate=%s error_type=%s",
            bullhorn_candidate_id,
            type(exc).__name__,
        )
        raise

    # A Bullhorn /query over Notes (personReference.id) can return the SAME note
    # twice on a join fan-out. Track ids added THIS batch so a duplicate in one
    # response isn't db.add-ed twice — the second add would collide on the unique
    # idempotency key at flush (session is autoflush=False, so the pending row is
    # invisible to _note_already_imported's query).
    added = 0
    added_ids: set = set()
    for note in notes:
        if not isinstance(note, dict):
            continue
        note_id = note.get("id")
        comments = str(note.get("comments") or "").strip()
        if note_id is None or not comments:
            continue
        if note_id in added_ids:
            continue
        if _note_already_imported(db, application_id=app.id, note_id=note_id):
            continue
        author = _note_author(note)
        meta = {
            "note": sanitize_text_for_storage(comments),
            "actor_name": author,
            "for_agent": True,
            "kind": "note",
            "source": "bullhorn",
            "bullhorn_note_id": note_id,
        }
        row = CandidateApplicationEvent(
            application_id=app.id,
            organization_id=app.organization_id,
            event_type=RECRUITER_NOTE_EVENT,
            actor_type="recruiter",
            reason=sanitize_text_for_storage(comments),
            event_metadata=meta,
            idempotency_key=_note_idempotency_key(note_id),
            created_at=now,
        )
        db.add(row)
        added_ids.add(note_id)
        added += 1
    if added:
        db.flush()
    return added


def _note_author(note: dict) -> str:
    person = note.get("commentingPerson")
    if isinstance(person, dict):
        name = person.get("name") or " ".join(
            p for p in (str(person.get("firstName") or ""), str(person.get("lastName") or "")) if p
        ).strip()
        if name:
            return sanitize_text_for_storage(str(name))
    return "Bullhorn"
