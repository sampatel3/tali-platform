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

from sqlalchemy.orm import Session

from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.document_service import sanitize_json_for_storage
from . import sync_candidates, sync_events, sync_jobs
from .local_write import bullhorn_status_overwrite_blocked
from .service import BullhornService
from .sync_service import JOB_ORDER_FIELDS, JOB_SUBMISSION_FIELDS

logger = logging.getLogger("taali.bullhorn.reconcile")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_millis(dt: datetime) -> int:
    """Bullhorn ``dateLastModified`` is epoch MILLISECONDS."""
    return int(dt.timestamp() * 1000)


# --- dateLastModified fallback sweep -----------------------------------------


def sweep_modified_since(
    db: Session, org: Organization, *, client: BullhornService, since: datetime, now: datetime | None = None
) -> dict:
    """Repair role closure + re-sync submissions modified since ``since``.

    Walks open JobOrders → their JobSubmissions with ``dateLastModified >= since``
    → the same candidate/application upsert + history + notes the full walk uses.
    The local-write-wins guard is honoured so a just-written-back status isn't
    clobbered. Returns counters.
    """
    now = now or _now()
    watermark = _epoch_millis(since)
    counters: dict[str, int | str] = {
        "job_orders": 0,
        "submissions": 0,
        "applications": 0,
        "roles_closed": 0,
        "errors": 0,
    }

    # Fetch and validate the ENTIRE remote open set before any missing-id close
    # is allowed. A page/API/shape failure raises and preserves every local role.
    job_orders = client.search_open_job_orders_complete(fields=JOB_ORDER_FIELDS)
    _open_ids, repair = sync_jobs.repair_roles_from_complete_open_snapshot(
        db,
        org,
        job_orders,
        closed_at=now,
    )
    counters["roles_closed"] = repair["roles_closed"]
    _record_job_order_repair(db, org, repair, source="modified_since_sweep", checked_at=now)
    for job_order in job_orders:
        job_order_id = str(job_order.get("id") or "").strip()
        if not job_order_id.isdigit():
            continue
        try:
            # Keep the JobOrder itself current (spec edits ride dateLastModified too).
            role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
            if role is None:
                raise ValueError("JobOrder could not be materialized")
            db.commit()
            counters["job_orders"] += 1
            applied, submission_errors, submissions_seen = _sweep_job_order_submissions(
                db, org, role=role, job_order_id=job_order_id, watermark=watermark, client=client, now=now
            )
            counters["applications"] += applied
            counters["submissions"] += submissions_seen
            counters["errors"] += submission_errors
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
    watermark: int,
    client: BullhornService,
    now: datetime,
) -> tuple[int, int, int]:
    """Return ``(applied, errors, seen)`` for one JobOrder's submissions."""
    where = (
        f"jobOrder.id={int(job_order_id)} AND isDeleted=false "
        f"AND dateLastModified>={watermark}"
    )
    submissions = client.query_job_submissions(fields=JOB_SUBMISSION_FIELDS, where=where)
    applied = 0
    errors = 0
    seen = 0
    for submission in submissions:
        if not isinstance(submission, dict) or submission.get("isDeleted"):
            continue
        # Defensive re-scope (never trust the transport to have filtered).
        sub_order_id = str((submission.get("jobOrder") or {}).get("id") or "").strip()
        if sub_order_id and sub_order_id != job_order_id:
            continue
        seen += 1
        try:
            _guard_local_status(db, org, submission)
            candidate_payload = _resolve_candidate_payload(client, submission)
            sync_candidates.sync_submission(
                db=db,
                org=org,
                role=role,
                submission=submission,
                candidate_payload=candidate_payload,
                client=client,
                now=now,
            )
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
                    db=db, app=app, submission_id=submission_id, client=client
                )
                bullhorn_candidate_id = str((submission.get("candidate") or {}).get("id") or "").strip()
                if bullhorn_candidate_id:
                    sync_events.import_notes(
                        db=db,
                        app=app,
                        bullhorn_candidate_id=bullhorn_candidate_id,
                        client=client,
                        now=now,
                    )
            db.commit()
            applied += 1
        except Exception as exc:  # pragma: no cover — isolate a bad submission
            db.rollback()
            errors += 1
            logger.error(
                "Bullhorn sweep submission failed org_id=%s submission_id=%s error_type=%s",
                org.id,
                submission.get("id"),
                type(exc).__name__,
            )
    return applied, errors, seen


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


def _resolve_candidate_payload(client: BullhornService, submission: dict) -> dict:
    nested = submission.get("candidate")
    if isinstance(nested, dict) and (nested.get("email") or nested.get("firstName") or nested.get("name")):
        return nested
    cand_id = str((nested or {}).get("id") or "").strip()
    if not cand_id.isdigit():
        raise ValueError("JobSubmission candidate id is missing")
    rows = client.search_candidates(fields=sync_candidates.CANDIDATE_FIELDS, query=f"id:{cand_id}")
    matched = next((r for r in rows if str(r.get("id")) == cand_id), None)
    if matched is None:
        raise LookupError(f"Bullhorn Candidate {cand_id} was not returned")
    return matched


# --- count-based reconciliation ----------------------------------------------


def reconcile_counts(db: Session, org: Organization, *, client: BullhornService) -> dict:
    """Repair missed JobOrder closes, then compare remote vs local counts.

    The open JobOrder snapshot must be proven complete before missing local ids
    are soft-closed. It then records any remaining mismatch on
    ``org.bullhorn_last_sync_summary['reconciliation']``. Candidate/application
    state is not mutated by this pass.
    """
    now = _now()
    remote_job_orders = client.search_open_job_orders_complete(fields="id,isOpen")
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
    )
    remote_submissions = _count_open_submissions(client, remote_job_order_ids)

    local_roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.bullhorn_job_order_id.isnot(None),
            Role.workable_job_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .count()
    )
    local_apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.source == "bullhorn",
            CandidateApplication.deleted_at.is_(None),
        )
        .count()
    )

    entities = {
        "job_orders": {"remote": len(remote_job_order_ids), "local": int(local_roles)},
        "job_submissions": {"remote": int(remote_submissions), "local": int(local_apps)},
    }
    discrepancies = {
        name: counts
        for name, counts in entities.items()
        if counts["remote"] != counts["local"]
    }
    summary = {
        "checked_at": now.isoformat(),
        "entities": entities,
        "discrepancies": discrepancies,
        "job_order_repair": repair_telemetry,
        "ok": not discrepancies,
    }
    _record_reconciliation(db, org, summary)
    if discrepancies:
        logger.warning(
            "Bullhorn reconciliation discrepancy org_id=%s: %s", org.id, discrepancies
        )
    return summary


def _count_open_submissions(client: BullhornService, job_order_ids: set[str]) -> int:
    """Count non-deleted JobSubmissions across the open JobOrders (paged read)."""
    total = 0
    for job_order_id in job_order_ids:
        if not str(job_order_id).isdigit():
            continue
        rows = client.query_job_submissions(
            fields="id", where=f"jobOrder.id={int(job_order_id)} AND isDeleted=false"
        )
        total += len(rows)
    return total


def _record_job_order_repair(
    db: Session,
    org: Organization,
    repair: dict[str, int],
    *,
    source: str,
    checked_at: datetime,
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
    org.bullhorn_last_sync_summary = sanitize_json_for_storage(
        {**existing, "job_order_repair": telemetry}
    )
    db.add(org)
    db.commit()
    return telemetry


def _record_reconciliation(db: Session, org: Organization, summary: dict) -> None:
    """Merge the reconciliation block into ``bullhorn_last_sync_summary``."""
    existing = org.bullhorn_last_sync_summary if isinstance(org.bullhorn_last_sync_summary, dict) else {}
    merged = {**existing, "reconciliation": summary}
    org.bullhorn_last_sync_summary = sanitize_json_for_storage(merged)
    db.add(org)
    db.commit()
