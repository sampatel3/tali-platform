"""Negative reconciliation for complete Bullhorn candidate-note snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.candidate_application_event import CandidateApplicationEvent
from ....models.role import Role
from ....services.application_notes import RECRUITER_NOTE_EVENT
from ....services.document_service import sanitize_json_for_storage

BULLHORN_NOTE_REVOKED_EVENT = "bullhorn_note_revoked"


def normalize_bullhorn_id(value: object) -> str | None:
    """Return one canonical positive ASCII Bullhorn entity id."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).isascii()
        or not str(value).isdigit()
        or int(value) <= 0
    ):
        return None
    return str(int(value))


def _note_key(note_id: object) -> str:
    normalized = normalize_bullhorn_id(note_id)
    if normalized is None:
        raise ValueError("Bullhorn Note id must be a positive integer")
    return f"bullhorn_note:{normalized}"


def _row_is_active(row: CandidateApplicationEvent) -> bool:
    metadata = row.event_metadata or {}
    return bool(
        metadata.get("for_agent") is not False
        and metadata.get("revoked") is not True
        and metadata.get("superseded") is not True
    )


def _matching_note_rows(
    db: Session,
    *,
    org_id: int,
    note_id: str,
) -> list[CandidateApplicationEvent]:
    normalized_note_id = normalize_bullhorn_id(note_id)
    if normalized_note_id is None:
        return []
    key = _note_key(normalized_note_id)
    candidates = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            or_(
                CandidateApplicationEvent.idempotency_key == key,
                CandidateApplicationEvent.idempotency_key.like(f"{key}:%"),
            ),
        )
        .order_by(CandidateApplicationEvent.id.asc())
        .all()
    )
    return [
        row
        for row in candidates
        if normalize_bullhorn_id(
            (row.event_metadata or {}).get("bullhorn_note_id")
        )
        == normalized_note_id
        and (row.event_metadata or {}).get("source") == "bullhorn"
    ]


def _revoke_rows(
    db: Session,
    *,
    rows: list[CandidateApplicationEvent],
    org_id: int,
    note_id: str,
    now: datetime,
    source: str,
) -> int:
    revoked = 0
    by_application: dict[int, list[CandidateApplicationEvent]] = {}
    for row in rows:
        by_application.setdefault(int(row.application_id), []).append(row)
    for application_id, revisions in by_application.items():
        active_revisions = [row for row in revisions if _row_is_active(row)]
        if not active_revisions:
            continue
        for row in active_revisions:
            metadata = dict(row.event_metadata or {})
            row.event_metadata = sanitize_json_for_storage(
                {
                    **metadata,
                    "for_agent": False,
                    "revoked": True,
                    "revoked_at": now.isoformat(),
                    "revocation_source": source,
                }
            )
        db.add(
            CandidateApplicationEvent(
                application_id=application_id,
                organization_id=org_id,
                event_type=BULLHORN_NOTE_REVOKED_EVENT,
                actor_type="sync",
                reason="Bullhorn note revoked",
                event_metadata={
                    "bullhorn_note_id": note_id,
                    "source": "bullhorn",
                    "revoked_note_event_ids": [row.id for row in active_revisions],
                },
                idempotency_key=(
                    f"bullhorn_note_revoked:{note_id}:"
                    + hashlib.sha256(
                        ",".join(str(row.id) for row in active_revisions).encode(
                            "ascii"
                        )
                    ).hexdigest()[:12]
                ),
                created_at=now,
            )
        )
        revoked += 1
    if revoked:
        db.flush()
    return revoked


def revoke_note_placements(
    *,
    db: Session,
    org_id: int,
    note_id: str,
    now: datetime,
    source: str,
    application_ids: set[int] | None = None,
) -> int:
    """Revoke selected active placements while preserving historical revisions."""
    normalized_note_id = normalize_bullhorn_id(note_id)
    if normalized_note_id is None:
        return 0
    rows = _matching_note_rows(
        db,
        org_id=org_id,
        note_id=normalized_note_id,
    )
    if application_ids is not None:
        rows = [row for row in rows if int(row.application_id) in application_ids]
    return _revoke_rows(
        db,
        rows=rows,
        org_id=org_id,
        note_id=normalized_note_id,
        now=now,
        source=source,
    )


def eligible_note_applications(
    db: Session,
    *,
    org_id: int,
    bullhorn_candidate_id: str,
) -> list[CandidateApplication]:
    """Live target apps where Bullhorn is allowed to provide note context.

    Closed Bullhorn roles deliberately remain included: their still-live
    applications are directly readable, so missed Note deletes must continue to
    reconcile there. Workable application/role identity retains authority.
    """
    candidate_id = normalize_bullhorn_id(bullhorn_candidate_id)
    if candidate_id is None:
        return []
    rows = (
        db.query(CandidateApplication, Role)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == org_id,
            Candidate.bullhorn_candidate_id == candidate_id,
            Role.organization_id == org_id,
        )
        .order_by(CandidateApplication.id.asc())
        .all()
    )
    return [
        app
        for app, role in rows
        if not str(app.workable_candidate_id or "").strip()
        and not str(role.workable_job_id or "").strip()
        and str(app.source or "").strip().lower() != "workable"
    ]


def active_candidate_note_ids(
    db: Session,
    *,
    org_id: int,
    bullhorn_candidate_id: str,
) -> set[str]:
    """Canonical ids for active imported notes currently placed on a candidate."""
    candidate_id = normalize_bullhorn_id(bullhorn_candidate_id)
    if candidate_id is None:
        return set()
    rows = (
        db.query(CandidateApplicationEvent)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplication.organization_id == org_id,
            Candidate.organization_id == org_id,
            Candidate.bullhorn_candidate_id == candidate_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            CandidateApplicationEvent.idempotency_key.like("bullhorn_note:%"),
        )
        .order_by(CandidateApplicationEvent.id.asc())
        .all()
    )
    note_ids: set[str] = set()
    for row in rows:
        metadata = row.event_metadata or {}
        note_id = normalize_bullhorn_id(metadata.get("bullhorn_note_id"))
        if (
            note_id is not None
            and metadata.get("source") == "bullhorn"
            and _row_is_active(row)
        ):
            note_ids.add(note_id)
    return note_ids


def active_note_application_ids(
    db: Session,
    *,
    org_id: int,
    note_id: str,
) -> set[int]:
    """Applications that currently expose one imported Bullhorn note."""
    return {
        int(row.application_id)
        for row in _matching_note_rows(db, org_id=org_id, note_id=note_id)
        if _row_is_active(row)
    }


def org_note_reconciliation_candidate_ids(
    db: Session,
    *,
    org_id: int,
) -> tuple[set[str], set[str]]:
    """Return candidate ids to snapshot and unscoped active note ids to exact-read."""
    app_rows = (
        db.query(CandidateApplication, Candidate, Role)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == org_id,
            Role.organization_id == org_id,
        )
        .all()
    )
    candidate_ids: set[str] = set()
    for app, candidate, role in app_rows:
        candidate_id = normalize_bullhorn_id(candidate.bullhorn_candidate_id)
        bullhorn_owned = bool(
            str(app.bullhorn_job_submission_id or "").strip()
            and not str(app.workable_candidate_id or "").strip()
            and not str(role.workable_job_id or "").strip()
            and str(app.source or "").strip().lower() != "workable"
        )
        if candidate_id is not None and bullhorn_owned:
            candidate_ids.add(candidate_id)

    note_rows = (
        db.query(CandidateApplicationEvent, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == org_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            CandidateApplicationEvent.idempotency_key.like("bullhorn_note:%"),
        )
        .all()
    )
    unscoped_note_ids: set[str] = set()
    for row, candidate in note_rows:
        metadata = row.event_metadata or {}
        note_id = normalize_bullhorn_id(metadata.get("bullhorn_note_id"))
        if (
            note_id is None
            or metadata.get("source") != "bullhorn"
            or not _row_is_active(row)
        ):
            continue
        candidate_id = normalize_bullhorn_id(candidate.bullhorn_candidate_id)
        if candidate_id is None:
            unscoped_note_ids.add(note_id)
        else:
            candidate_ids.add(candidate_id)
    return candidate_ids, unscoped_note_ids


def revoke_note(
    *,
    db: Session,
    org_id: int,
    note_id: str,
    now: datetime,
) -> int:
    """Revoke one event-identified note across its tenant."""
    return revoke_note_placements(
        db=db,
        org_id=org_id,
        note_id=note_id,
        now=now,
        source="bullhorn_delete_event",
    )


def reconcile_candidate_note_snapshot(
    *,
    db: Session,
    org_id: int,
    bullhorn_candidate_id: str,
    remote_note_ids: set[str],
    confirmed_missing_note_ids: set[str],
    now: datetime,
) -> int:
    """Revoke only snapshot-missing notes independently confirmed destructive.

    Stable pagination totals alone are not snapshot isolation. Requiring the
    caller's exact-confirmed id set makes the old unsafe complete-snapshot-only
    invocation impossible.
    """
    candidate_id = normalize_bullhorn_id(bullhorn_candidate_id)
    normalized_remote = {
        note_id
        for value in remote_note_ids
        if (note_id := normalize_bullhorn_id(value)) is not None
    }
    normalized_confirmed = {
        note_id
        for value in confirmed_missing_note_ids
        if (note_id := normalize_bullhorn_id(value)) is not None
    }
    if candidate_id is None or len(normalized_remote) != len(remote_note_ids):
        raise ValueError("Bullhorn Note snapshot ids are invalid")
    if len(normalized_confirmed) != len(confirmed_missing_note_ids):
        raise ValueError("Bullhorn confirmed missing Note ids are invalid")
    if normalized_confirmed.intersection(normalized_remote):
        raise ValueError("confirmed missing Bullhorn Notes cannot be snapshot-present")
    rows = (
        db.query(CandidateApplicationEvent)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplication.organization_id == org_id,
            Candidate.organization_id == org_id,
            Candidate.bullhorn_candidate_id == candidate_id,
            CandidateApplicationEvent.event_type == RECRUITER_NOTE_EVENT,
            CandidateApplicationEvent.idempotency_key.like("bullhorn_note:%"),
        )
        .order_by(CandidateApplicationEvent.id.asc())
        .all()
    )
    missing: dict[str, list[CandidateApplicationEvent]] = {}
    for row in rows:
        metadata = row.event_metadata or {}
        note_id = normalize_bullhorn_id(metadata.get("bullhorn_note_id"))
        if metadata.get("source") != "bullhorn" or note_id is None:
            continue
        if note_id not in normalized_confirmed:
            continue
        missing.setdefault(note_id, []).append(row)

    revoked = 0
    for note_id, revisions in missing.items():
        revoked += _revoke_rows(
            db,
            rows=revisions,
            org_id=org_id,
            note_id=note_id,
            now=now,
            source="bullhorn_complete_candidate_snapshot",
        )
    return revoked
