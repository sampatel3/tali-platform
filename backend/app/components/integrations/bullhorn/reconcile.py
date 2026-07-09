"""Bullhorn fallback sweep + count-based reconciliation.

Two safety nets under the event-driven incremental path:

1. :func:`sweep_modified_since` — a ``dateLastModified`` incremental sweep. Events
   are the primary path, but they can be MISSED (subscription gap during an
   outage, a dropped batch, ordering is undocumented). This re-fetches every
   open JobOrder and, for each, the JobSubmissions modified since a watermark,
   and runs them back through the full-sync upsert helpers. It is a FALLBACK and
   carries NO delete semantics — ``dateLastModified`` can't express a deletion
   (deletes surface only via events), so this sweep only inserts/updates.

2. :func:`reconcile_counts` — a nightly count-based check. For each entity type it
   compares the remote count (a paged read) against the local active-row count
   and records any discrepancy on ``org.bullhorn_last_sync_summary`` so drift is
   visible instead of silent. It does not mutate candidate/application state — it
   surfaces, the operator (or a full sync) resolves.

Cost safety: the sweep reuses ``sync_submission`` (scoring gated to the CREATE
branch + ``starred_for_auto_sync``); reconciliation is read-only counting. No
paid re-evaluation of stale scores is ever triggered here.
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
    """Re-sync JobSubmissions modified since ``since`` (fallback; no deletes).

    Walks open JobOrders → their JobSubmissions with ``dateLastModified >= since``
    → the same candidate/application upsert + history + notes the full walk uses.
    The local-write-wins guard is honoured so a just-written-back status isn't
    clobbered. Returns counters.
    """
    now = now or _now()
    watermark = _epoch_millis(since)
    counters: dict[str, int] = {"job_orders": 0, "submissions": 0, "applications": 0, "errors": 0}

    job_orders = client.search_job_orders(fields=JOB_ORDER_FIELDS, query="isOpen:true")
    for job_order in job_orders:
        job_order_id = str(job_order.get("id") or "").strip()
        if not job_order_id.isdigit():
            continue
        try:
            # Keep the JobOrder itself current (spec edits ride dateLastModified too).
            role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
            if role is None:
                continue
            db.commit()
            counters["job_orders"] += 1
            counters["applications"] += _sweep_job_order_submissions(
                db, org, role=role, job_order_id=job_order_id, watermark=watermark, client=client, now=now
            )
        except Exception:  # pragma: no cover — isolate a bad job order
            db.rollback()
            counters["errors"] += 1
            logger.exception(
                "Bullhorn sweep failed org_id=%s job_order_id=%s", org.id, job_order_id
            )
    counters["status"] = "ok"
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
) -> int:
    """Upsert submissions of one JobOrder modified since the watermark. Returns count."""
    where = (
        f"jobOrder.id={int(job_order_id)} AND isDeleted=false "
        f"AND dateLastModified>={watermark}"
    )
    submissions = client.query_job_submissions(fields=JOB_SUBMISSION_FIELDS, where=where)
    applied = 0
    for submission in submissions:
        if not isinstance(submission, dict) or submission.get("isDeleted"):
            continue
        # Defensive re-scope (never trust the transport to have filtered).
        sub_order_id = str((submission.get("jobOrder") or {}).get("id") or "").strip()
        if sub_order_id and sub_order_id != job_order_id:
            continue
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
        except Exception:  # pragma: no cover — isolate a bad submission
            db.rollback()
            logger.exception(
                "Bullhorn sweep submission failed org_id=%s submission_id=%s",
                org.id,
                submission.get("id"),
            )
    return applied


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
        return nested if isinstance(nested, dict) else {}
    try:
        rows = client.search_candidates(fields=sync_candidates.CANDIDATE_FIELDS, query=f"id:{cand_id}")
    except Exception:  # pragma: no cover
        logger.exception("Bullhorn candidate re-fetch failed id=%s", cand_id)
        return nested if isinstance(nested, dict) else {}
    matched = next((r for r in rows if str(r.get("id")) == cand_id), None)
    return matched or (nested if isinstance(nested, dict) else {})


# --- count-based reconciliation ----------------------------------------------


def reconcile_counts(db: Session, org: Organization, *, client: BullhornService) -> dict:
    """Compare remote vs local counts per entity; surface discrepancies.

    Read-only. For each tracked entity, count the remote rows (a paged read over
    the same open-scope the sync mirrors) and the local active rows, and record
    any mismatch on ``org.bullhorn_last_sync_summary['reconciliation']`` so a
    silent drift becomes visible. Does not mutate application/candidate state.
    """
    now = _now()
    remote_job_orders = client.search_job_orders(fields="id", query="isOpen:true")
    remote_job_order_ids = [str(r.get("id")) for r in remote_job_orders if r.get("id") is not None]
    remote_submissions = _count_open_submissions(client, remote_job_order_ids)

    local_roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.source == "bullhorn",
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
        "ok": not discrepancies,
    }
    _record_reconciliation(db, org, summary)
    if discrepancies:
        logger.warning(
            "Bullhorn reconciliation discrepancy org_id=%s: %s", org.id, discrepancies
        )
    return summary


def _count_open_submissions(client: BullhornService, job_order_ids: list[str]) -> int:
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


def _record_reconciliation(db: Session, org: Organization, summary: dict) -> None:
    """Merge the reconciliation block into ``bullhorn_last_sync_summary``."""
    existing = org.bullhorn_last_sync_summary if isinstance(org.bullhorn_last_sync_summary, dict) else {}
    merged = {**existing, "reconciliation": summary}
    org.bullhorn_last_sync_summary = sanitize_json_for_storage(merged)
    db.add(org)
    db.commit()
