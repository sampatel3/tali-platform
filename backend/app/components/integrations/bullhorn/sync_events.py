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

import hashlib
import json
import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import append_application_event
from ....models.candidate_application import CandidateApplication
from ....models.candidate_application_event import CandidateApplicationEvent
from ....services.application_notes import RECRUITER_NOTE_EVENT
from ....services.document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .note_reconciliation import (
    active_candidate_note_ids,
    active_note_application_ids,
    eligible_note_applications,
    normalize_bullhorn_id,
    org_note_reconciliation_candidate_ids,
    revoke_note as revoke_reconciled_note,
    revoke_note_placements,
)
from .service import BullhornService

logger = logging.getLogger(__name__)

# Read contracts (Bullhorn returns only requested fields).
SUBMISSION_HISTORY_FIELDS = "id,status,dateAdded,modifyingUser,jobSubmission"
NOTE_FIELDS = "id,comments,action,dateAdded,commentingPerson,personReference"

BULLHORN_STATUS_CHANGE_EVENT = "bullhorn_status_change"


def _history_idempotency_key(history_id: object) -> str:
    return f"bullhorn_jsh:{history_id}"


def _note_idempotency_key(note_id: object) -> str:
    normalized = normalize_bullhorn_id(note_id)
    if normalized is None:
        raise ValueError("Bullhorn Note id must be a positive integer")
    return f"bullhorn_note:{normalized}"


def import_submission_history(
    *,
    db: Session,
    app: CandidateApplication,
    submission_id: str,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> int:
    """Append JobSubmissionHistory rows as application events. Returns count added.

    Append-only + idempotent: the history-row id anchors the idempotency key, so
    ``append_application_event`` returns the existing row on a re-sync instead of
    inserting a duplicate. The Bullhorn status string is preserved verbatim in
    ``to_stage`` (the remote status) and metadata — this is an audit trail of the
    remote pipeline, not a Taali stage transition.
    """
    guard = provider_guard or (lambda: None)
    guard()
    try:
        history = client.get_job_submission_history_complete(
            job_submission_id=submission_id, fields=SUBMISSION_HISTORY_FIELDS
        )
    except Exception as exc:
        guard()
        logger.error(
            "Bullhorn JobSubmissionHistory read failed submission=%s error_type=%s",
            submission_id,
            type(exc).__name__,
        )
        raise
    guard()

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


def _note_revision_rows(
    db: Session,
    *,
    application_id: int,
    note_id: object,
) -> list[CandidateApplicationEvent]:
    normalized_note_id = normalize_bullhorn_id(note_id)
    if normalized_note_id is None:
        return []
    key = _note_idempotency_key(normalized_note_id)
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            or_(
                CandidateApplicationEvent.idempotency_key == key,
                CandidateApplicationEvent.idempotency_key.like(f"{key}:%"),
            ),
        )
        .order_by(CandidateApplicationEvent.id.asc())
        .all()
    )
    expected = normalized_note_id
    return [
        row
        for row in rows
        if normalize_bullhorn_id(
            (row.event_metadata or {}).get("bullhorn_note_id")
        )
        == expected
    ]


def _note_revision_fingerprint(*, comments: str, author: str, action: object) -> str:
    canonical = json.dumps(
        {
            "comments": comments,
            "author": author,
            "action": str(action or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _stored_note_fingerprint(row: CandidateApplicationEvent) -> str:
    metadata = dict(row.event_metadata or {})
    stored = str(metadata.get("revision_fingerprint") or "")
    if stored:
        return stored
    return _note_revision_fingerprint(
        comments=str(metadata.get("note") or row.reason or "").strip(),
        author=str(metadata.get("actor_name") or "Bullhorn"),
        action=metadata.get("bullhorn_action"),
    )


def _revision_key(
    db: Session,
    *,
    application_id: int,
    note_id: object,
    fingerprint: str,
    first_revision: bool,
) -> str:
    base = (
        _note_idempotency_key(note_id)
        if first_revision
        else f"{_note_idempotency_key(note_id)}:revision:{fingerprint}"
    )
    candidate = base
    suffix = 1
    while (
        db.query(CandidateApplicationEvent.id)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.idempotency_key == candidate,
        )
        .first()
        is not None
    ):
        suffix += 1
        candidate = f"{base}:{suffix}"
    return candidate


def _normalized_note_payload(note: dict) -> dict:
    note_id = normalize_bullhorn_id(note.get("id"))
    person = note.get("personReference")
    person_id = normalize_bullhorn_id(
        person.get("id") if isinstance(person, dict) else None
    )
    if (
        note_id is None
        or person_id is None
        or not isinstance(note.get("comments"), str)
    ):
        raise ValueError("Bullhorn Note payload is invalid")
    return {
        **note,
        "id": note_id,
        "personReference": {**person, "id": person_id},
    }


def _upsert_note_revision(
    *,
    db: Session,
    app: CandidateApplication,
    note: dict,
    now: datetime,
) -> bool:
    note = _normalized_note_payload(note)
    note_id = note["id"]
    comments = str(note.get("comments") or "").strip()
    author = _note_author(note)
    fingerprint = _note_revision_fingerprint(
        comments=comments,
        author=author,
        action=note.get("action"),
    )
    revisions = _note_revision_rows(
        db,
        application_id=app.id,
        note_id=note_id,
    )
    active = [
        row
        for row in revisions
        if (row.event_metadata or {}).get("for_agent") is not False
        and (row.event_metadata or {}).get("revoked") is not True
        and (row.event_metadata or {}).get("superseded") is not True
    ]
    matching = [row for row in active if _stored_note_fingerprint(row) == fingerprint]
    if matching:
        keep = matching[-1]
        # Repair any historical duplicate-active state while keeping the
        # repeated remote UPDATE itself idempotent.
        for row in active:
            if row.id == keep.id:
                continue
            metadata = dict(row.event_metadata or {})
            row.event_metadata = sanitize_json_for_storage(
                {
                    **metadata,
                    "for_agent": False,
                    "superseded": True,
                    "superseded_at": now.isoformat(),
                }
            )
        return False

    for row in active:
        metadata = dict(row.event_metadata or {})
        row.event_metadata = sanitize_json_for_storage(
            {
                **metadata,
                "for_agent": False,
                "superseded": True,
                "superseded_at": now.isoformat(),
                "superseded_by_fingerprint": fingerprint,
            }
        )

    meta = {
        "note": sanitize_text_for_storage(comments),
        "actor_name": author,
        "for_agent": True,
        "kind": "note",
        "source": "bullhorn",
        "bullhorn_note_id": note_id,
        "bullhorn_action": note.get("action"),
        "revision": len(revisions) + 1,
        "revision_fingerprint": fingerprint,
    }
    db.add(
        CandidateApplicationEvent(
            application_id=app.id,
            organization_id=app.organization_id,
            event_type=RECRUITER_NOTE_EVENT,
            actor_type="recruiter",
            reason=sanitize_text_for_storage(comments),
            event_metadata=sanitize_json_for_storage(meta),
            idempotency_key=_revision_key(
                db,
                application_id=app.id,
                note_id=note_id,
                fingerprint=fingerprint,
                first_revision=not revisions,
            ),
            created_at=now,
        )
    )
    return True


def apply_exact_note(
    *,
    db: Session,
    org_id: int,
    note: dict,
    now: datetime,
) -> dict[str, int]:
    """Apply one exact Note to every eligible app and repair stale placements."""
    normalized = _normalized_note_payload(note)
    note_id = normalized["id"]
    candidate_id = normalized["personReference"]["id"]
    targets = eligible_note_applications(
        db,
        org_id=org_id,
        bullhorn_candidate_id=candidate_id,
    )
    target_ids = {int(app.id) for app in targets}
    active_application_ids = active_note_application_ids(
        db,
        org_id=org_id,
        note_id=note_id,
    )
    comments = normalized["comments"].strip()
    if not comments:
        revoked = revoke_note_placements(
            db=db,
            org_id=org_id,
            note_id=note_id,
            now=now,
            source="bullhorn_blank_note",
        )
        return {"created": 0, "revoked": revoked}

    revoked = revoke_note_placements(
        db=db,
        org_id=org_id,
        note_id=note_id,
        now=now,
        source="bullhorn_note_authority_changed",
        application_ids=active_application_ids - target_ids,
    )
    created = sum(
        int(_upsert_note_revision(db=db, app=app, note=normalized, now=now))
        for app in targets
    )
    if created or revoked:
        db.flush()
    return {"created": created, "revoked": revoked}


def _read_exact_note(
    *,
    client: BullhornService,
    note_id: str,
    provider_guard: Callable[[], None] | None,
) -> dict | None:
    guard = provider_guard or (lambda: None)
    guard()
    try:
        note = client.get_note_exact(note_id, fields=NOTE_FIELDS)
    except Exception:
        guard()
        raise
    guard()
    return note


def reconcile_candidate_notes(
    *,
    db: Session,
    org_id: int,
    bullhorn_candidate_id: str,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Apply one complete candidate snapshot with exact missing-id confirmation."""
    candidate_id = normalize_bullhorn_id(bullhorn_candidate_id)
    if candidate_id is None:
        raise ValueError("Bullhorn candidate id must be a positive integer")
    guard = provider_guard or (lambda: None)
    guard()
    try:
        notes = client.query_notes_complete(
            candidate_id=candidate_id,
            fields=NOTE_FIELDS,
        )
    except Exception as exc:
        guard()
        logger.error(
            "Bullhorn Notes read failed candidate=%s error_type=%s",
            candidate_id,
            type(exc).__name__,
        )
        raise
    guard()

    counters = {"created": 0, "revoked": 0, "exact_confirmations": 0}
    remote_active_ids: set[str] = set()
    seen_ids: set[str] = set()
    for raw_note in notes:
        if not isinstance(raw_note, dict):
            raise ValueError("Bullhorn complete Note snapshot contained an invalid row")
        note = _normalized_note_payload(raw_note)
        note_id = note["id"]
        if note["personReference"]["id"] != candidate_id:
            raise ValueError("Bullhorn complete Note snapshot violated candidate scope")
        if note_id in seen_ids:
            continue
        seen_ids.add(note_id)
        applied = apply_exact_note(
            db=db,
            org_id=org_id,
            note=note,
            now=now,
        )
        counters["created"] += applied["created"]
        counters["revoked"] += applied["revoked"]
        if note["comments"].strip():
            remote_active_ids.add(note_id)

    local_active_ids = active_candidate_note_ids(
        db,
        org_id=org_id,
        bullhorn_candidate_id=candidate_id,
    )
    for note_id in sorted(local_active_ids - remote_active_ids, key=int):
        exact = _read_exact_note(
            client=client,
            note_id=note_id,
            provider_guard=provider_guard,
        )
        counters["exact_confirmations"] += 1
        if exact is None:
            counters["revoked"] += revoke_note_placements(
                db=db,
                org_id=org_id,
                note_id=note_id,
                now=now,
                source="bullhorn_snapshot_confirmed_absent",
            )
            continue
        applied = apply_exact_note(
            db=db,
            org_id=org_id,
            note=exact,
            now=now,
        )
        counters["created"] += applied["created"]
        counters["revoked"] += applied["revoked"]
    return counters


def reconcile_org_note_authority(
    *,
    db: Session,
    org_id: int,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> dict[str, int | str | bool]:
    """Reconcile every locally relevant candidate, including closed-role apps.

    Work is bounded by durable local authority: one complete provider read per
    represented candidate, then exact reads only for ids missing from that
    candidate snapshot. No arbitrary row cap can silently leave context stale.
    """
    candidate_ids, unscoped_note_ids = org_note_reconciliation_candidate_ids(
        db,
        org_id=org_id,
    )
    totals = {
        "status": "ok",
        "ok": True,
        "candidates": len(candidate_ids),
        "created": 0,
        "revoked": 0,
        "exact_confirmations": 0,
    }
    for candidate_id in sorted(candidate_ids, key=int):
        result = reconcile_candidate_notes(
            db=db,
            org_id=org_id,
            bullhorn_candidate_id=candidate_id,
            client=client,
            now=now,
            provider_guard=provider_guard,
        )
        for key in ("created", "revoked", "exact_confirmations"):
            totals[key] = int(totals[key]) + result[key]

    for note_id in sorted(unscoped_note_ids, key=int):
        exact = _read_exact_note(
            client=client,
            note_id=note_id,
            provider_guard=provider_guard,
        )
        totals["exact_confirmations"] = int(totals["exact_confirmations"]) + 1
        if exact is None:
            totals["revoked"] = int(totals["revoked"]) + revoke_note_placements(
                db=db,
                org_id=org_id,
                note_id=note_id,
                now=now,
                source="bullhorn_snapshot_confirmed_absent",
            )
            continue
        applied = apply_exact_note(
            db=db,
            org_id=org_id,
            note=exact,
            now=now,
        )
        totals["created"] = int(totals["created"]) + applied["created"]
        totals["revoked"] = int(totals["revoked"]) + applied["revoked"]
    return totals


def import_notes(
    *,
    db: Session,
    app: CandidateApplication,
    bullhorn_candidate_id: str,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> int:
    """Import and reconcile one candidate's complete Note authority snapshot."""
    result = reconcile_candidate_notes(
        db=db,
        org_id=int(app.organization_id),
        bullhorn_candidate_id=bullhorn_candidate_id,
        client=client,
        now=now,
        provider_guard=provider_guard,
    )
    if result["created"] or result["revoked"]:
        db.flush()
    return result["created"]


def upsert_note_revision(
    *,
    db: Session,
    app: CandidateApplication,
    note: dict,
    now: datetime,
) -> bool:
    """Apply one already strict-refetched Note to one application."""
    if not isinstance(note, dict):
        raise ValueError("Bullhorn Note payload is invalid")
    return _upsert_note_revision(
        db=db,
        app=app,
        note=_normalized_note_payload(note),
        now=now,
    )


def revoke_note(
    *,
    db: Session,
    org_id: int,
    note_id: str,
    now: datetime,
) -> int:
    """Revoke imported note context while retaining an append-only audit tombstone."""
    return revoke_reconciled_note(db=db, org_id=org_id, note_id=note_id, now=now)


def _note_author(note: dict) -> str:
    person = note.get("commentingPerson")
    if isinstance(person, dict):
        name = person.get("name") or " ".join(
            p for p in (str(person.get("firstName") or ""), str(person.get("lastName") or "")) if p
        ).strip()
        if name:
            return sanitize_text_for_storage(str(name))
    return "Bullhorn"
