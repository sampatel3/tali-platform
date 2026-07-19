"""Bullhorn fallback sweep + count-based reconciliation.

Two safety nets under the event-driven incremental path:

1. :func:`sweep_modified_since` — a ``dateLastModified`` incremental sweep. Events
   are the primary path, but they can be MISSED (subscription gap during an
   outage, a dropped batch, ordering is undocumented). It first obtains a
   proven-complete, paginated snapshot of every open JobOrder and soft-closes
   active local Bullhorn roles absent from that set. It then runs the open rows
   and modified submissions back through the full-sync upsert helpers.

2. :func:`reconcile_counts` — a nightly repair + count check. It independently
   validates the complete open JobOrder set and applies the same missing-role
   close repair, then compares remote/local entity counts and records remaining
   drift on ``org.bullhorn_last_sync_summary``.

Cost safety: the sweep reuses ``sync_submission`` (scoring gated to the CREATE
branch + ``starred_for_auto_sync``). Role closure and count reconciliation do
not trigger paid re-evaluation of stale scores.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.document_service import sanitize_json_for_storage
from . import sync_candidates, sync_events, sync_jobs
from .errors import BullhornApiError
from .local_write import bullhorn_status_overwrite_blocked
from .service import BullhornService
from .sync_service import (
    JOB_ORDER_FIELDS,
    JOB_SUBMISSION_FIELDS,
    BullhornSyncLeaseLost,
)

logger = logging.getLogger("taali.bullhorn.reconcile")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_millis(dt: datetime) -> int:
    """Bullhorn ``dateLastModified`` is epoch MILLISECONDS."""
    return int(dt.timestamp() * 1000)


# --- dateLastModified fallback sweep -----------------------------------------


def sweep_modified_since(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    since: datetime,
    now: datetime | None = None,
    rehydrate_all: bool = False,
    defer_note_reconciliation: bool = False,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
    """Repair role closure + re-sync submissions modified since ``since``.

    Walks open JobOrders → their JobSubmissions with ``dateLastModified >= since``
    → the same candidate/application upsert + history + notes the full walk uses.
    The local-write-wins guard is honoured so a just-written-back status isn't
    clobbered. ``defer_note_reconciliation`` is reserved for the gap runner,
    which immediately follows this sweep with one org pass per represented
    candidate; it avoids repeating the same Note snapshot per submission.
    Returns counters.
    """
    now = now or _now()
    watermark = _epoch_millis(since)
    counters: dict[str, int | str] = {
        "job_orders": 0,
        "submissions": 0,
        "applications": 0,
        "applications_deleted": 0,
        "roles_closed": 0,
        "errors": 0,
    }

    # Fetch and validate the ENTIRE remote open set before any missing-id close
    # is allowed. A page/API/shape failure raises and preserves every local role.
    guard = provider_guard or (lambda: None)
    guard()
    job_orders = client.search_open_job_orders_complete(fields=JOB_ORDER_FIELDS)
    guard()
    _open_ids, repair = sync_jobs.repair_roles_from_complete_open_snapshot(
        db,
        org,
        job_orders,
        closed_at=now,
    )
    counters["roles_closed"] = repair["roles_closed"]
    _record_job_order_repair(
        db,
        org,
        repair,
        source="modified_since_sweep",
        checked_at=now,
        provider_guard=provider_guard,
    )
    for job_order in job_orders:
        guard()
        job_order_id = str(job_order.get("id") or "").strip()
        if not job_order_id.isdigit():
            continue
        try:
            # Keep the JobOrder itself current (spec edits ride dateLastModified too).
            role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
            if role is None:
                raise ValueError("JobOrder could not be materialized")
            if str(role.workable_job_id or "").strip():
                counters["job_orders"] += 1
                continue
            guard()
            db.commit()
            counters["job_orders"] += 1
            (
                applied,
                deleted,
                submission_errors,
                submissions_seen,
            ) = _sweep_job_order_submissions(
                db,
                org,
                role=role,
                job_order_id=job_order_id,
                watermark=None if rehydrate_all else watermark,
                client=client,
                now=now,
                import_candidate_notes=not defer_note_reconciliation,
                provider_guard=provider_guard,
            )
            counters["applications"] += applied
            counters["applications_deleted"] += deleted
            counters["submissions"] += submissions_seen
            counters["errors"] += submission_errors
        except BullhornSyncLeaseLost:
            db.rollback()
            raise
        except Exception as exc:  # pragma: no cover — isolate a bad job order
            db.rollback()
            counters["errors"] += 1
            logger.error(
                "Bullhorn sweep failed org_id=%s job_order_id=%s error_type=%s",
                org.id,
                job_order_id,
                type(exc).__name__,
            )
    counters["status"] = "retry_pending" if counters["errors"] else "ok"
    return counters


def _sweep_job_order_submissions(
    db: Session,
    org: Organization,
    *,
    role: Role,
    job_order_id: str,
    watermark: int | None,
    client: BullhornService,
    now: datetime,
    import_candidate_notes: bool = True,
    provider_guard: Callable[[], None] | None = None,
) -> tuple[int, int, int, int]:
    """Return ``(applied, deleted, errors, seen)`` for one JobOrder snapshot."""
    guard = provider_guard or (lambda: None)
    guard()
    submissions = client.query_job_submissions_complete(
        job_order_id=job_order_id,
        fields=JOB_SUBMISSION_FIELDS,
        modified_since_millis=watermark,
        include_deleted=True,
    )
    guard()
    applied = 0
    deleted = 0
    errors = 0
    seen = 0
    for submission in submissions:
        guard()
        seen += 1
        try:
            if submission.get("isDeleted") is True:
                submission_id = str(submission.get("id") or "").strip()
                if sync_candidates.tombstone_submission(
                    db,
                    org,
                    submission_id=submission_id,
                    deleted_at=now,
                ):
                    guard()
                    db.commit()
                    deleted += 1
                continue
            _guard_local_status(db, org, submission)
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
                continue
            submission_id = str(submission.get("id") or "").strip()
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
                if bullhorn_candidate_id and import_candidate_notes:
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
            applied += 1
        except BullhornSyncLeaseLost:
            db.rollback()
            raise
        except Exception as exc:  # pragma: no cover — isolate a bad submission
            db.rollback()
            errors += 1
            logger.error(
                "Bullhorn sweep submission failed org_id=%s submission_id=%s error_type=%s",
                org.id,
                submission.get("id"),
                type(exc).__name__,
            )
    return applied, deleted, errors, seen


def _guard_local_status(db: Session, org: Organization, submission: dict) -> None:
    """Blank an inbound status the sweep must not overwrite (local-write-wins)."""
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
        submission["status"] = app.bullhorn_status or ""


def _resolve_candidate_payload(
    client: BullhornService,
    submission: dict,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
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


# --- count-based reconciliation ----------------------------------------------


def reconcile_counts(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
    """Repair missed JobOrder closes, then compare remote vs local counts.

    The open JobOrder snapshot must be proven complete before missing local ids
    are soft-closed. It then records any remaining mismatch on
    ``org.bullhorn_last_sync_summary['reconciliation']``. A Bullhorn-only active
    application absent from the proven complete active set is soft-deleted;
    Workable-linked applications and roles are never mutated.
    """
    now = _now()
    guard = provider_guard or (lambda: None)
    guard()
    remote_job_orders = client.search_open_job_orders_complete(fields="id,isOpen")
    guard()
    remote_job_order_ids, repair = sync_jobs.repair_roles_from_complete_open_snapshot(
        db,
        org,
        remote_job_orders,
        closed_at=now,
    )
    repair_telemetry = _record_job_order_repair(
        db,
        org,
        repair,
        source="count_reconciliation",
        checked_at=now,
        provider_guard=provider_guard,
    )
    remote_submission_ids = _complete_open_submission_ids(
        client,
        remote_job_order_ids,
        provider_guard=provider_guard,
    )
    repaired_submissions = sync_candidates.repair_missing_active_submissions(
        db,
        org,
        remote_submission_ids=remote_submission_ids,
        deleted_at=now,
        provider_guard=provider_guard,
    )
    local_role_rows = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.source == "bullhorn",
            Role.bullhorn_job_order_id.isnot(None),
            Role.workable_job_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    local_role_values = [
        str(role.bullhorn_job_order_id)
        for role in local_role_rows
        if str(role.bullhorn_job_order_id or "").isdigit()
    ]
    local_role_ids = set(local_role_values)
    local_submission_rows = (
        db.query(CandidateApplication.bullhorn_job_submission_id)
        .join(Role, CandidateApplication.role_id == Role.id)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.source == "bullhorn",
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.bullhorn_job_submission_id.isnot(None),
            Role.organization_id == org.id,
            Role.source == "bullhorn",
            Role.bullhorn_job_order_id.isnot(None),
            Role.workable_job_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    local_submission_values = [
        str(value)
        for (value,) in local_submission_rows
        if str(value or "").isdigit()
    ]
    local_submission_ids = set(local_submission_values)

    entities = {
        "job_orders": {
            "remote": len(remote_job_order_ids),
            "local": len(local_role_ids),
        },
        "job_submissions": {
            "remote": len(remote_submission_ids),
            "local": len(local_submission_ids),
        },
    }
    discrepancies = {}
    for name, remote_ids, local_ids, local_values, local_row_count in (
        (
            "job_orders",
            remote_job_order_ids,
            local_role_ids,
            local_role_values,
            len(local_role_rows),
        ),
        (
            "job_submissions",
            remote_submission_ids,
            local_submission_ids,
            local_submission_values,
            len(local_submission_rows),
        ),
    ):
        if (
            remote_ids == local_ids
            and len(local_values) == len(local_ids)
            and local_row_count == len(local_values)
        ):
            continue
        mismatch = dict(entities[name])
        if len(remote_ids) == len(local_ids):
            mismatch["id_set_mismatch"] = True
        if len(local_values) != len(local_ids):
            mismatch["duplicate_local_ids"] = True
        if local_row_count != len(local_values):
            mismatch["invalid_local_ids"] = True
        discrepancies[name] = mismatch

    summary = {
        "checked_at": now.isoformat(),
        "entities": entities,
        "discrepancies": discrepancies,
        "job_order_repair": repair_telemetry,
        "submission_repair": {
            "applications_deleted": repaired_submissions,
        },
        "ok": not discrepancies,
    }
    _record_reconciliation(
        db,
        org,
        summary,
        provider_guard=provider_guard,
    )
    if discrepancies:
        logger.warning(
            "Bullhorn reconciliation discrepancy org_id=%s: %s", org.id, discrepancies
        )
    return summary


def _complete_open_submission_ids(
    client: BullhornService,
    job_order_ids: set[str],
    *,
    provider_guard: Callable[[], None] | None = None,
) -> set[str]:
    guard = provider_guard or (lambda: None)
    submission_ids: set[str] = set()
    for job_order_id in job_order_ids:
        guard()
        rows = client.query_job_submissions_complete(
            job_order_id=job_order_id,
            fields="id,jobOrder,isDeleted",
        )
        guard()
        for row in rows:
            submission_id = str(int(row["id"]))
            if submission_id in submission_ids:
                raise BullhornApiError(
                    "Bullhorn complete JobSubmission snapshots contained a duplicate id"
                )
            submission_ids.add(submission_id)
    return submission_ids


def _record_job_order_repair(
    db: Session,
    org: Organization,
    repair: dict[str, int],
    *,
    source: str,
    checked_at: datetime,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
    """Persist sanitized, count-only visibility for a close-set repair."""
    telemetry = {
        "checked_at": checked_at.isoformat(),
        "source": source,
        "remote_open_count": int(repair.get("remote_open_count") or 0),
        "local_active_before": int(repair.get("local_active_before") or 0),
        "roles_closed": int(repair.get("roles_closed") or 0),
        "local_active_after": int(repair.get("local_active_after") or 0),
    }
    existing = (
        org.bullhorn_last_sync_summary
        if isinstance(org.bullhorn_last_sync_summary, dict)
        else {}
    )
    prior = existing.get("job_order_repair")
    visible = telemetry
    if (
        telemetry["roles_closed"] == 0
        and isinstance(prior, dict)
        and int(prior.get("roles_closed") or 0) > 0
    ):
        # Keep the last actionable repair visible; the current no-op check is
        # still recorded in the nested reconciliation result.
        visible = prior
    org.bullhorn_last_sync_summary = sanitize_json_for_storage(
        {**existing, "job_order_repair": visible}
    )
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return telemetry


def _record_reconciliation(
    db: Session,
    org: Organization,
    summary: dict,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> None:
    """Merge the reconciliation block into ``bullhorn_last_sync_summary``."""
    existing = org.bullhorn_last_sync_summary if isinstance(org.bullhorn_last_sync_summary, dict) else {}
    merged = {**existing, "reconciliation": summary}
    org.bullhorn_last_sync_summary = sanitize_json_for_storage(merged)
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
