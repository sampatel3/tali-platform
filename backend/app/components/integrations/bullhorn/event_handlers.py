"""Per-event entity handling for the Bullhorn incremental sync.

A Bullhorn event is a *dirty flag*, not a payload: it names the entity that
changed (``entityName`` + ``entityId``) and, for updates, the field NAMES that
changed (``updatedProperties``) — never the new values. So the only correct
response is to RE-FETCH the entity and run it back through the exact same
full-sync upsert helpers the batch importer uses (:mod:`sync_jobs`,
:mod:`sync_candidates`, :mod:`sync_events`). That guarantees an event-driven
update and a full re-sync converge to the same local state.

Entity → action:
* ``JobOrder``   INSERTED/UPDATED → re-fetch by id → :func:`sync_jobs.upsert_role_from_job_order`.
* ``JobSubmission`` INSERTED/UPDATED → re-fetch by id → resolve its candidate →
  :func:`sync_candidates.sync_submission` (+ history + notes), exactly like the
  full walk does for one submission.
* ``Candidate``  UPDATED → re-fetch by id → refresh the mirrored Candidate row
  in place (profile fields; NEVER re-scores — cost safety).
* ``Note``       INSERTED/UPDATED → re-import the candidate's notes for any local
  application (agent context), idempotent on the note id.
* ``*`` DELETED → soft-delete the local mirror (``deleted_at``), mirroring how
  the platform treats a remotely-vanished entity: the sync's ``deleted_at``
  filters then exclude it, same as a soft-deleted Workable role.

LOCAL-WRITE-WINS: an inbound JobSubmission update must not clobber a
``bullhorn_status`` that Taali itself just wrote back (a recruiter move/reject
in flight to Bullhorn). We honour ``bullhorn_status_local_write_at`` with the
same guard window Workable uses for ``workable_stage_local_write_at`` — inside
the window we skip re-applying a *different* remote status and leave the
locally-written one; the reconcile/full sweep settles it afterwards.

Cost safety (hard rule): re-fetch → upsert reuses ``sync_submission``, whose
scoring enqueue is gated on the CREATE branch + ``starred_for_auto_sync`` only.
An UPDATE to an existing application never re-enqueues paid scoring, and nothing
here dispatches re-evaluation of a stale score. A brand-new application arriving
by event is scored once, exactly like a brand-new application arriving by import.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.document_service import (
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.role_change_audit import (
    ROLE_CHANGE_ACTION_SOFT_DELETED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ....services.role_concurrency import bump_role_version
from ....services.role_lifecycle import stop_role_for_ats_deletion
from . import sync_candidates, sync_events, sync_jobs
from .local_write import bullhorn_status_overwrite_blocked
from .service import BullhornService
from .sync_events import NOTE_FIELDS
from .sync_service import (
    JOB_ORDER_FIELDS,
    JOB_SUBMISSION_FIELDS,
    BullhornSyncLeaseLost,
)

logger = logging.getLogger("taali.bullhorn.events")

# Entity names we act on. Everything else is ignored (a subscription is created
# for exactly these, but we stay defensive if the queue carries an extra name).
ENTITY_JOB_ORDER = "JobOrder"
ENTITY_JOB_SUBMISSION = "JobSubmission"
ENTITY_CANDIDATE = "Candidate"
ENTITY_NOTE = "Note"
SUBSCRIBED_ENTITIES = (ENTITY_JOB_ORDER, ENTITY_JOB_SUBMISSION, ENTITY_CANDIDATE, ENTITY_NOTE)

_DELETE_EVENT_TYPES = {"DELETED", "DELETE"}
MUTATION_EVENT_TYPES = frozenset({"INSERTED", "UPDATED", *_DELETE_EVENT_TYPES})


def normalize_event_type(event: object) -> str | None:
    """Return the mutation kind from official or legacy Bullhorn envelopes.

    Current Bullhorn events use ``eventType=ENTITY`` and carry the actual
    mutation in ``entityEventType``. Older fixtures and some integrations put
    the mutation directly in ``eventType``. Conflicting/malformed combinations
    fail closed so they cannot accidentally trigger an upsert or delete.
    """
    if not isinstance(event, dict):
        return None
    raw_envelope = event.get("eventType")
    raw_mutation = event.get("entityEventType")
    envelope = (
        raw_envelope.strip().upper() if isinstance(raw_envelope, str) else ""
    )
    mutation = (
        raw_mutation.strip().upper() if isinstance(raw_mutation, str) else ""
    )
    if envelope == "ENTITY":
        return mutation if mutation in MUTATION_EVENT_TYPES else None
    if envelope not in MUTATION_EVENT_TYPES:
        return None
    if mutation and mutation != envelope:
        return None
    return envelope


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dispatch_event(
    db: Session,
    org: Organization,
    event: dict,
    *,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Handle one Bullhorn event. Returns a short outcome tag for counters.

    Never raises into the poll loop — the caller has already checkpointed the
    batch's ``requestId``, so a per-event failure must not lose the rest of the
    batch. Isolates each event; logs + returns ``"error"`` on failure.
    """
    if not isinstance(event, dict):
        return "skipped"
    entity_name = str(event.get("entityName") or "").strip()
    entity_id = str(event.get("entityId") or "").strip()
    event_type = normalize_event_type(event)
    if not entity_name or not entity_id:
        return "skipped"
    if entity_name not in SUBSCRIBED_ENTITIES:
        return "skipped"
    if event_type is None:
        return "skipped"

    try:
        if event_type in _DELETE_EVENT_TYPES:
            return _handle_delete(
                db,
                org,
                entity_name,
                entity_id,
                now=now,
                provider_guard=provider_guard,
            )
        if entity_name == ENTITY_JOB_ORDER:
            return _handle_job_order(
                db,
                org,
                entity_id,
                client=client,
                now=now,
                provider_guard=provider_guard,
            )
        if entity_name == ENTITY_JOB_SUBMISSION:
            return _handle_job_submission(
                db,
                org,
                entity_id,
                client=client,
                now=now,
                provider_guard=provider_guard,
            )
        if entity_name == ENTITY_CANDIDATE:
            return _handle_candidate(
                db,
                org,
                entity_id,
                client=client,
                now=now,
                provider_guard=provider_guard,
            )
        if entity_name == ENTITY_NOTE:
            return _handle_note(
                db,
                org,
                entity_id,
                client=client,
                now=now,
                provider_guard=provider_guard,
            )
    except BullhornSyncLeaseLost:
        db.rollback()
        raise
    except Exception as exc:  # pragma: no cover — never break the batch on one event
        db.rollback()
        logger.error(
            "Bullhorn event handling failed org_id=%s entity=%s id=%s type=%s error_type=%s",
            org.id,
            entity_name,
            entity_id,
            event_type,
            type(exc).__name__,
        )
        return "error"
    return "skipped"


# --- INSERTED / UPDATED: re-fetch the entity, run the full-sync upsert --------


def _handle_job_order(
    db: Session,
    org: Organization,
    job_order_id: str,
    *,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Re-fetch one JobOrder by id → the same role upsert the full sync uses."""
    if not job_order_id.isdigit():
        return "skipped"
    existing_role = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.bullhorn_job_order_id == job_order_id,
        )
        .first()
    )
    if _workable_authoritative_role(existing_role):
        return "skipped"
    guard = provider_guard or (lambda: None)
    guard()
    job_order = client.get_job_order_exact(
        job_order_id,
        fields=JOB_ORDER_FIELDS,
    )
    guard()
    if job_order is None:
        # Vanished between event and fetch — treat as a delete (soft-delete mirror).
        return _handle_delete(
            db,
            org,
            ENTITY_JOB_ORDER,
            job_order_id,
            now=now,
            provider_guard=provider_guard,
        )
    if not _job_order_is_open(job_order):
        # A just-closed JobOrder (isOpen false / non-open status). The full sync
        # only imports ``isOpen:true`` orders, so an incremental UPDATE must NOT
        # reactivate a closed order (``upsert_role_from_job_order`` clears
        # ``deleted_at``). Route to the same soft-delete path a remote delete
        # uses so local state converges with a full re-sync.
        return _handle_delete(
            db,
            org,
            ENTITY_JOB_ORDER,
            job_order_id,
            now=now,
            provider_guard=provider_guard,
        )
    role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
    if role is None:
        return "skipped"
    guard()
    db.commit()
    return "job_order"


def _handle_job_submission(
    db: Session,
    org: Organization,
    submission_id: str,
    *,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Re-fetch one JobSubmission by id → resolve candidate → full submission upsert.

    Mirrors ``BullhornSyncService._sync_one_job_order``'s per-submission body:
    upsert candidate+application, then import that submission's history + the
    candidate's notes. The local-write-wins guard is applied to the remote status
    BEFORE the upsert so a just-written-back status isn't overwritten.
    """
    if not submission_id.isdigit():
        return "skipped"
    guard = provider_guard or (lambda: None)
    guard()
    submission = client.get_job_submission_exact(
        submission_id,
        fields=JOB_SUBMISSION_FIELDS,
    )
    guard()
    if submission is None or submission.get("isDeleted") is not False:
        return _handle_delete(
            db,
            org,
            ENTITY_JOB_SUBMISSION,
            submission_id,
            now=now,
            provider_guard=provider_guard,
        )

    role = _role_for_submission(db, org, submission)
    if role is None:
        # The parent JobOrder isn't mirrored yet (e.g. a closed order we don't
        # track). Skip — a JobOrder event or the full sweep will bring it in.
        return "skipped"

    # LOCAL-WRITE-WINS: if Taali just wrote this submission's status back, drop
    # the remote status from THIS event so the upsert keeps the local value.
    _apply_local_write_guard(db, org, submission)

    candidate_payload = _resolve_candidate_payload(
        client,
        submission,
        provider_guard=provider_guard,
    )
    sync_result = sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=client,
        now=now,
        provider_guard=provider_guard,
    )
    if sync_result.get("authority_skipped"):
        return "skipped"
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.bullhorn_job_submission_id == submission_id,
        )
        .first()
    )
    if app is not None:
        sync_events.import_submission_history(
            db=db,
            app=app,
            submission_id=submission_id,
            client=client,
            provider_guard=provider_guard,
        )
        bullhorn_candidate_id = str((submission.get("candidate") or {}).get("id") or "").strip()
        if bullhorn_candidate_id:
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id=bullhorn_candidate_id,
                client=client,
                now=now,
                provider_guard=provider_guard,
            )
    guard()
    db.commit()
    return "job_submission"


def _handle_candidate(
    db: Session,
    org: Organization,
    candidate_id: str,
    *,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Refresh the mirrored Candidate's profile fields in place (never re-scores).

    We only touch a Candidate we already mirror — an event for a candidate with
    no local row and no application is irrelevant until a JobSubmission brings
    them in. Cost safety: this refreshes profile fields only; it does NOT enqueue
    scoring or paid re-evaluation.
    """
    if not candidate_id.isdigit():
        return "skipped"
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org.id,
            Candidate.bullhorn_candidate_id == candidate_id,
        )
        .first()
    )
    if candidate is None:
        return "skipped"
    if _candidate_has_non_bullhorn_authority(db, org, candidate):
        return "skipped"
    guard = provider_guard or (lambda: None)
    guard()
    payload = client.get_candidate_exact(
        candidate_id,
        fields=sync_candidates.CANDIDATE_FIELDS,
    )
    guard()
    if payload is None:
        return _handle_delete(
            db,
            org,
            ENTITY_CANDIDATE,
            candidate_id,
            now=now,
            provider_guard=provider_guard,
        )
    _refresh_candidate_fields(candidate, payload)
    guard()
    db.commit()
    return "candidate"


def _handle_note(
    db: Session,
    org: Organization,
    note_id: str,
    *,
    client: BullhornService,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Apply one exact Note and repair every stale or ineligible placement."""
    normalized_note_id = sync_events.normalize_bullhorn_id(note_id)
    if normalized_note_id is None:
        return "skipped"
    note = _note_payload(
        client,
        normalized_note_id,
        provider_guard=provider_guard,
    )
    if note is None:
        return _handle_delete(
            db,
            org,
            ENTITY_NOTE,
            normalized_note_id,
            now=now,
            provider_guard=provider_guard,
        )
    applied = sync_events.apply_exact_note(
        db=db,
        org_id=int(org.id),
        note=note,
        now=now,
    )
    if applied["created"] or applied["revoked"]:
        guard = provider_guard or (lambda: None)
        guard()
        db.commit()
        return "note"
    return "skipped"


# --- DELETED: soft-delete the local mirror ------------------------------------


def _handle_delete(
    db: Session,
    org: Organization,
    entity_name: str,
    entity_id: str,
    *,
    now: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Soft-delete the local mirror of a remotely-deleted entity.

    Deletes surface ONLY via events in Bullhorn, so this is the only path that
    removes a row. We soft-delete (stamp ``deleted_at``) rather than hard-delete,
    mirroring the platform's treatment of a vanished remote entity — the sync's
    ``deleted_at.is_(None)`` filters then exclude it everywhere, exactly like a
    soft-deleted Workable role. Idempotent: a row already soft-deleted stays so.
    """
    stamp = now or _now()
    guard = provider_guard or (lambda: None)
    if entity_name == ENTITY_JOB_ORDER:
        locked = (
            db.query(Role.id, Role.version)
            .filter(
                Role.organization_id == org.id,
                Role.bullhorn_job_order_id == entity_id,
            )
            .order_by(Role.id.asc())
            .with_for_update(of=Role)
            .first()
        )
        if locked is None:
            return "skipped"
        role = db.get(Role, int(locked.id))
        if role is None:
            return "skipped"
        if _workable_authoritative_role(role):
            return "skipped"
        if int(role.version or 1) != int(locked.version or 1):
            db.refresh(role)
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        closed = sync_jobs.soft_close_role(role, closed_at=stamp)
        stopped = stop_role_for_ats_deletion(
            role,
            deleted_at=stamp,
            provider="Bullhorn",
        )
        if closed or stopped:
            audit_to = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=audit_before,
                action=ROLE_CHANGE_ACTION_SOFT_DELETED,
                actor_user_id=None,
                from_version=audit_from,
                to_version=audit_to,
                reason="Bullhorn job deleted or closed; agent turned off",
                request_id=f"bullhorn-job-delete:{entity_id}",
            )
            guard()
            db.commit()
            return "deleted_role"
        return "skipped"
    if entity_name == ENTITY_JOB_SUBMISSION:
        if sync_candidates.tombstone_submission(
            db,
            org,
            submission_id=entity_id,
            deleted_at=stamp,
        ):
            guard()
            db.commit()
            return "deleted_application"
        return "skipped"
    if entity_name == ENTITY_CANDIDATE:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == org.id,
                Candidate.bullhorn_candidate_id == entity_id,
            )
            .first()
        )
        if candidate is not None and _candidate_has_non_bullhorn_authority(
            db,
            org,
            candidate,
        ):
            return "skipped"
        if candidate is not None and candidate.deleted_at is None:
            candidate.deleted_at = stamp
            guard()
            db.commit()
            return "deleted_candidate"
        return "skipped"
    if entity_name == ENTITY_NOTE:
        revoked = sync_events.revoke_note(
            db=db,
            org_id=int(org.id),
            note_id=entity_id,
            now=stamp,
        )
        if revoked:
            guard()
            db.commit()
            return "revoked_note"
        return "skipped"
    return "skipped"


# --- helpers ------------------------------------------------------------------


def _job_order_is_open(job_order: dict) -> bool:
    """True when a re-fetched JobOrder is still open (matches the full sync).

    The full sync only imports ``isOpen:true`` orders, so an incremental event
    must treat a non-open order as a close, not an active upsert. Bullhorn always
    returns ``isOpen`` in ``JOB_ORDER_FIELDS``; when present it is authoritative.
    Only when it's absent do we fall back to being permissive (treat as open) so
    a malformed payload never silently closes a live role.
    """
    is_open = job_order.get("isOpen")
    if is_open is None:
        return True
    if isinstance(is_open, str):
        return is_open.strip().lower() not in {"false", "0", "no", ""}
    return bool(is_open)


def _role_for_submission(db: Session, org: Organization, submission: dict) -> Role | None:
    job_order_id = str((submission.get("jobOrder") or {}).get("id") or "").strip()
    if not job_order_id:
        return None
    return (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.bullhorn_job_order_id == job_order_id,
            Role.workable_job_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .first()
    )


def _workable_authoritative_role(role: Role | None) -> bool:
    return bool(role is not None and str(role.workable_job_id or "").strip())


def _workable_authoritative_application(
    db: Session,
    app: CandidateApplication,
) -> bool:
    if str(app.workable_candidate_id or "").strip():
        return True
    if str(app.source or "").strip().lower() == "workable":
        return True
    role = db.get(Role, app.role_id)
    return _workable_authoritative_role(role)


def _candidate_has_non_bullhorn_authority(
    db: Session,
    org: Organization,
    candidate: Candidate,
) -> bool:
    """Whether globally hiding this person would hide another live authority."""
    if str(candidate.workable_candidate_id or "").strip():
        return True
    live = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    return any(
        str(app.workable_candidate_id or "").strip()
        or not str(app.bullhorn_job_submission_id or "").strip()
        or str(role.workable_job_id or "").strip()
        or not str(role.bullhorn_job_order_id or "").strip()
        or str(app.source or "").strip().lower() != "bullhorn"
        for app, role in live
    )


def _resolve_candidate_payload(
    client: BullhornService,
    submission: dict,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
    """Fetch the submission's Candidate profile by id (id-only association → full)."""
    nested = submission.get("candidate")
    cand_id = str((nested or {}).get("id") or "").strip()
    if not cand_id.isdigit():
        raise ValueError("JobSubmission candidate id is missing")
    guard = provider_guard or (lambda: None)
    guard()
    matched = client.get_candidate_exact(
        cand_id,
        fields=sync_candidates.CANDIDATE_FIELDS,
    )
    guard()
    if matched is None:
        raise LookupError(f"Bullhorn Candidate {cand_id} was not returned")
    return matched


def _refresh_candidate_fields(candidate: Candidate, payload: dict) -> None:
    """Update mirrored profile fields from a re-fetched Candidate payload."""
    candidate.deleted_at = None
    email = payload.get("email")
    if isinstance(email, str) and "@" in email and "." in email:
        candidate.email = sanitize_text_for_storage(email.strip().lower())
    name = payload.get("name") or " ".join(
        p for p in (str(payload.get("firstName") or ""), str(payload.get("lastName") or "")) if p
    ).strip()
    if isinstance(name, str) and name.strip():
        candidate.full_name = sanitize_text_for_storage(name.strip())
    occupation = payload.get("occupation")
    if isinstance(occupation, str) and occupation.strip():
        candidate.position = sanitize_text_for_storage(occupation.strip())
    phone = payload.get("phone") or payload.get("mobile")
    if isinstance(phone, str) and phone.strip():
        candidate.phone = sanitize_text_for_storage(phone.strip())
    candidate.bullhorn_data = sanitize_json_for_storage(payload)


def _note_payload(
    client: BullhornService,
    note_id: str,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> dict | None:
    """Read one full Note once through the strict typed event read."""
    guard = provider_guard or (lambda: None)
    guard()
    try:
        data = client.get_note_exact(
            note_id,
            fields=f"{NOTE_FIELDS}",
        )
    except Exception:
        guard()
        raise
    guard()
    if data is None:
        return None
    return data


# --- local-write-wins ---------------------------------------------------------


def _apply_local_write_guard(db: Session, org: Organization, submission: dict) -> None:
    """Drop the remote status from an inbound event when Taali just wrote it back.

    Looks up the local application for this submission; if a Taali write-back is
    inside the guard window and the event's status DIFFERS, we blank the event's
    ``status`` so the downstream upsert keeps the locally-written value. Same
    semantics as Workable's ``_stage_overwrite_blocked``.
    """
    submission_id = str(submission.get("id") or "").strip()
    if not submission_id:
        return
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.bullhorn_job_submission_id == submission_id,
        )
        .first()
    )
    if app is None:
        return
    remote_status = str(submission.get("status") or "").strip()
    if bullhorn_status_overwrite_blocked(app, remote_status):
        logger.info(
            "Bullhorn local-write-wins: keeping local status for app_id=%s, ignoring remote %r",
            app.id,
            remote_status,
        )
        # Force the upsert to keep the local status: mirror the current local
        # value onto the event so ``_apply_stage_mapping`` re-applies a no-op.
        submission["status"] = app.bullhorn_status or ""
