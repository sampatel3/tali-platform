"""Durable Celery fan-out for manual pre-screen batches and process cascades."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from .celery_app import celery_app
from .prescreen_root_dispatch import (
    PRESCREEN_ACTIVE_RUN_STATUSES,
    PRESCREEN_ROOT_DISPATCH_LIMIT,
    claim_dispatchable_prescreen_runs as _claim_dispatchable_prescreen_runs,
    clear_root_dispatch_lease as _clear_root_dispatch_lease,
    defer_root_dispatch_claim as _defer_root_dispatch_claim,
    prescreen_item_conflict_code as _prescreen_item_conflict_code,
    prescreen_item_run_authority as _prescreen_item_run_authority,
    prior_overlapping_prescreen_run_id as _prior_overlapping_run_id,
)


logger = logging.getLogger("taali.tasks.prescreen")

# The item task's hard limit is 270 seconds. A ten-minute lease leaves room for
# queue latency while still recovering a broker-accepted-but-lost publication
# promptly. Dispatch/recovery is bounded so one large role cannot flood Redis.
PRESCREEN_DISPATCH_LEASE = timedelta(minutes=10)
PRESCREEN_DISPATCH_RETRY_DELAY = timedelta(minutes=1)
PRESCREEN_PROVIDER_ATTEMPT_STALE_AFTER = timedelta(minutes=6)
PRESCREEN_DISPATCH_BATCH_LIMIT = 200
def select_prescreen_target_ids(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    refresh: bool,
) -> list[int]:
    """Select the exact current target set without loading CV bodies."""

    from ..models.candidate_application import CandidateApplication
    from ..services.pre_screen_retry_policy import (
        pre_screen_error_retry_due_clause,
    )

    query = db.query(CandidateApplication.id).filter(
        CandidateApplication.role_id == int(role_id),
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.cv_text.isnot(None),
        CandidateApplication.cv_text != "",
    )
    if not refresh:
        query = query.filter(
            or_(
                CandidateApplication.pre_screen_run_at.is_(None),
                and_(
                    CandidateApplication.cv_uploaded_at.isnot(None),
                    CandidateApplication.pre_screen_run_at.isnot(None),
                    CandidateApplication.cv_uploaded_at
                    > CandidateApplication.pre_screen_run_at,
                ),
                pre_screen_error_retry_due_clause(CandidateApplication),
            )
        )
    return [int(value) for (value,) in query.order_by(CandidateApplication.id).all()]


def _refresh_run_progress(run_id: int) -> None:
    from ..models.background_job_run import BackgroundJobRun
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_AMBIGUOUS,
        PRESCREEN_BATCH_ITEM_DONE,
        PRESCREEN_BATCH_ITEM_ERROR,
        PRESCREEN_BATCH_ITEM_SKIPPED,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        run = (
            db.query(BackgroundJobRun)
            .filter(BackgroundJobRun.id == int(run_id))
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return
        grouped = dict(
            db.query(PrescreenBatchItem.status, func.count(PrescreenBatchItem.id))
            .filter(PrescreenBatchItem.run_id == int(run_id))
            .group_by(PrescreenBatchItem.status)
            .all()
        )
        total = sum(int(value) for value in grouped.values())
        done = int(grouped.get(PRESCREEN_BATCH_ITEM_DONE, 0))
        ambiguous = int(grouped.get(PRESCREEN_BATCH_ITEM_AMBIGUOUS, 0))
        errors = int(grouped.get(PRESCREEN_BATCH_ITEM_ERROR, 0)) + ambiguous
        skipped = int(grouped.get(PRESCREEN_BATCH_ITEM_SKIPPED, 0))
        processed = done + errors + skipped
        counters = dict(run.counters or {})
        counters.update(
            {
                "total": total,
                "processed": processed,
                "succeeded": done,
                "errors": errors,
                "ambiguous": ambiguous,
                "skipped": skipped,
            }
        )
        run.counters = counters
        if total == 0 or processed >= total:
            run.status = "completed" if not errors else "completed_with_errors"
            run.finished_at = datetime.now(timezone.utc)
        elif run.status in {"queued", "dispatching"}:
            run.status = "running"
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("pre-screen run progress refresh failed run_id=%s error_type=%s", run_id, type(exc).__name__)
    finally:
        db.close()


def _claim_dispatchable_items(
    *,
    limit: int,
    run_id: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Lease queued items with ``FOR UPDATE SKIP LOCKED``.

    The lease is committed before publication. Concurrent Beat instances cannot
    claim the same item; a publisher crash or ambiguous broker acknowledgement
    merely leaves a lease that becomes eligible again after expiry.
    """

    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_QUEUED,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal

    claimed_at = now or datetime.now(timezone.utc)
    limit = max(1, min(int(limit), PRESCREEN_DISPATCH_BATCH_LIMIT))
    db = SessionLocal()
    try:
        query = (
            db.query(PrescreenBatchItem, BackgroundJobRun)
            .join(
                BackgroundJobRun,
                BackgroundJobRun.id == PrescreenBatchItem.run_id,
            )
            .filter(
                PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_QUEUED,
                or_(
                    PrescreenBatchItem.dispatch_lease_until.is_(None),
                    PrescreenBatchItem.dispatch_lease_until <= claimed_at,
                ),
                BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
            )
        )
        if run_id is not None:
            query = query.filter(BackgroundJobRun.id == int(run_id))
        rows = (
            query.order_by(PrescreenBatchItem.id)
            .with_for_update(of=PrescreenBatchItem, skip_locked=True)
            .limit(limit)
            .all()
        )
        claims: list[dict] = []
        for item, run in rows:
            token = str(uuid4())
            item.dispatch_token = token
            item.dispatch_lease_until = claimed_at + PRESCREEN_DISPATCH_LEASE
            item.dispatch_attempts = int(item.dispatch_attempts or 0) + 1
            item.last_dispatched_at = claimed_at
            item.error_code = None
            claims.append(
                {
                    "item_id": int(item.id),
                    "token": token,
                    "refresh": bool((run.counters or {}).get("refresh", False)),
                }
            )
        db.commit()
        return claims
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _defer_dispatch_claim(item_id: int, token: str) -> bool:
    """CAS a failed/ambiguous publication onto a short retry lease.

    A client-side publish exception does not prove the broker rejected the
    message. Keeping a lease prevents an immediate second publication; if the
    first message did arrive, its worker wins the item lock and this update
    becomes a harmless no-op after the item turns terminal.
    """

    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_QUEUED,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal

    values = {
        PrescreenBatchItem.dispatch_lease_until: (
            datetime.now(timezone.utc) + PRESCREEN_DISPATCH_RETRY_DELAY
        ),
        PrescreenBatchItem.error_code: "broker_dispatch_retry",
    }
    db = SessionLocal()
    try:
        updated = (
            db.query(PrescreenBatchItem)
            .filter(
                PrescreenBatchItem.id == int(item_id),
                PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_QUEUED,
                PrescreenBatchItem.dispatch_token == str(token),
            )
            .update(values, synchronize_session=False)
        )
        db.commit()
        return bool(updated)
    except Exception as exc:
        db.rollback()
        logger.error("pre-screen dispatch claim defer failed item_id=%s error_type=%s", item_id, type(exc).__name__)
        return False
    finally:
        db.close()


def dispatch_prescreen_queued_items(
    *,
    limit: int = PRESCREEN_DISPATCH_BATCH_LIMIT,
    run_id: int | None = None,
) -> dict:
    """Claim and publish a bounded set of recoverable pre-screen items."""

    claims = _claim_dispatchable_items(limit=limit, run_id=run_id)
    enqueued = 0
    dispatch_errors = 0
    for claim in claims:
        try:
            pre_screen_application_job.apply_async(
                args=(claim["item_id"],),
                kwargs={"refresh": claim["refresh"]},
            )
        except Exception as exc:
            dispatch_errors += 1
            logger.error("pre-screen item dispatch failed run_id=%s item_id=%s error_type=%s", run_id, claim["item_id"], type(exc).__name__)
            _defer_dispatch_claim(claim["item_id"], claim["token"])
        else:
            enqueued += 1
            # Leave the lease in place. If the broker loses the accepted
            # message, expiry recovers it. Once a delivery begins, the worker
            # commits an ``attempting`` token before network I/O; duplicate
            # deliveries observe that marker and never enter the paid call.
    return {
        "status": "ok" if not dispatch_errors else "partial",
        "claimed": len(claims),
        "enqueued": enqueued,
        "dispatch_errors": dispatch_errors,
        "run_id": int(run_id) if run_id is not None else None,
    }


def reap_ambiguous_prescreen_attempts(
    *,
    limit: int = PRESCREEN_DISPATCH_BATCH_LIMIT,
    now: datetime | None = None,
) -> dict:
    """Surface stale paid-call attempts without automatically paying again."""

    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_AMBIGUOUS,
        PRESCREEN_BATCH_ITEM_ATTEMPTING,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal

    checked_at = now or datetime.now(timezone.utc)
    cutoff = checked_at - PRESCREEN_PROVIDER_ATTEMPT_STALE_AFTER
    limit = max(1, min(int(limit), PRESCREEN_DISPATCH_BATCH_LIMIT))
    db = SessionLocal()
    run_ids: set[int] = set()
    try:
        rows = (
            db.query(PrescreenBatchItem)
            .join(
                BackgroundJobRun,
                BackgroundJobRun.id == PrescreenBatchItem.run_id,
            )
            .filter(
                PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
                or_(
                    PrescreenBatchItem.provider_attempt_started_at.is_(None),
                    PrescreenBatchItem.provider_attempt_started_at <= cutoff,
                ),
                BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
            )
            .order_by(PrescreenBatchItem.id)
            .with_for_update(of=PrescreenBatchItem, skip_locked=True)
            .limit(limit)
            .all()
        )
        for item in rows:
            run_ids.add(int(item.run_id))
            item.status = PRESCREEN_BATCH_ITEM_AMBIGUOUS
            item.error_code = "provider_attempt_outcome_unknown"
            item.finished_at = checked_at
            item.dispatch_token = None
            item.dispatch_lease_until = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for claimed_run_id in run_ids:
        _refresh_run_progress(claimed_run_id)
    return {"ambiguous": len(run_ids) if not rows else len(rows), "run_ids": sorted(run_ids)}


def dispatch_prescreen_batch_roots(
    *,
    limit: int = PRESCREEN_ROOT_DISPATCH_LIMIT,
    run_id: int | None = None,
) -> dict:
    """Publish leased root jobs; ambiguous failures remain Beat-recoverable."""
    claims = _claim_dispatchable_prescreen_runs(limit=limit, run_id=run_id)
    enqueued = dispatch_errors = 0
    for claim in claims:
        try:
            batch_pre_screen_role_job.apply_async(
                args=(claim["role_id"], claim["organization_id"]),
                kwargs={
                    "run_id": claim["run_id"],
                    "refresh": claim["refresh"],
                },
            )
        except Exception as exc:
            dispatch_errors += 1
            logger.error(
                "pre-screen root dispatch failed run_id=%s error_type=%s",
                claim["run_id"],
                type(exc).__name__,
            )
            if not _defer_root_dispatch_claim(claim["run_id"], claim["token"]):
                logger.error(
                    "pre-screen root dispatch defer failed run_id=%s error_code=root_dispatch_defer_failed",
                    claim["run_id"],
                )
        else:
            enqueued += 1
    return {
        "status": "ok" if not dispatch_errors else "recovering",
        "claimed": len(claims),
        "enqueued": enqueued,
        "dispatch_errors": dispatch_errors,
        "run_id": int(run_id) if run_id is not None else None,
    }


@celery_app.task(
    name="app.tasks.prescreen_tasks.batch_pre_screen_role_job",
    queue="scoring",
    acks_late=True,
    reject_on_worker_lost=True,
)
def batch_pre_screen_role_job(
    role_id: int,
    organization_id: int,
    *,
    run_id: int,
    refresh: bool = False,
) -> dict:
    """Materialize durable items, then fan them out to bounded workers."""

    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_QUEUED,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        run = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.organization_id == int(organization_id),
                BackgroundJobRun.scope_id == int(role_id),
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return {"status": "missing_run", "run_id": int(run_id)}
        if run.finished_at is not None:
            run.counters = _clear_root_dispatch_lease(run.counters, state="terminal")
            db.commit()
            return {
                "status": "already_terminal",
                "run_id": int(run_id),
                "run_status": run.status,
            }
        if not isinstance(run.counters, dict):
            raise ValueError("pre-screen batch counters must be an object")
        canonical_run_id = _prior_overlapping_run_id(db, run)
        if canonical_run_id is not None:
            counters = _clear_root_dispatch_lease(run.counters, state="duplicate")
            counters.update(
                duplicate_of_run_id=canonical_run_id,
                total=0,
                processed=0,
                succeeded=0,
                errors=0,
                skipped=0,
            )
            run.counters = counters
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": "duplicate_active_run",
                "run_id": int(run_id),
                "canonical_run_id": canonical_run_id,
            }
        durable_refresh = bool(run.counters.get("refresh", False))
        if bool(refresh) != durable_refresh:
            logger.warning("pre-screen batch ignored stale refresh run_id=%s", run_id)
        target_ids = select_prescreen_target_ids(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
            refresh=durable_refresh,
        )
        existing = {
            int(value)
            for (value,) in db.query(PrescreenBatchItem.application_id)
            .filter(PrescreenBatchItem.run_id == int(run_id))
            .all()
        }
        for application_id in target_ids:
            if application_id in existing:
                continue
            db.add(
                PrescreenBatchItem(
                    run_id=int(run_id),
                    organization_id=int(organization_id),
                    role_id=int(role_id),
                    application_id=application_id,
                    status=PRESCREEN_BATCH_ITEM_QUEUED,
                )
            )
        db.flush()
        materialized_total = int(
            db.query(func.count(PrescreenBatchItem.id))
            .filter(PrescreenBatchItem.run_id == int(run_id))
            .scalar()
            or 0
        )
        run.status = "running"
        counters = _clear_root_dispatch_lease(run.counters, state="started")
        counters.update({"refresh": durable_refresh, "total": materialized_total})
        run.counters = counters
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("pre-screen batch materialization failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        try:
            run = db.query(BackgroundJobRun).filter(
                BackgroundJobRun.id == int(run_id)
            ).one_or_none()
            if run is not None:
                run.status = "failed"
                run.error = "pre_screen_batch_materialization_failed"
                run.counters = _clear_root_dispatch_lease(run.counters, state="failed")
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
        return {"status": "error", "run_id": int(run_id)}
    finally:
        db.close()

    dispatched = dispatch_prescreen_queued_items(
        run_id=int(run_id),
        limit=PRESCREEN_DISPATCH_BATCH_LIMIT,
    )
    _refresh_run_progress(int(run_id))
    return {
        "status": (
            "enqueued"
            if dispatched["enqueued"] and not dispatched["dispatch_errors"]
            else dispatched["status"]
        ),
        "run_id": int(run_id),
        "claimed": dispatched["claimed"],
        "enqueued": dispatched["enqueued"],
        "dispatch_errors": dispatched["dispatch_errors"],
    }


@celery_app.task(
    name="app.tasks.prescreen_tasks.recover_prescreen_batch_dispatches",
    queue="scoring",
)
def recover_prescreen_batch_dispatches(
    *, limit: int = PRESCREEN_DISPATCH_BATCH_LIMIT
) -> dict:
    """Re-dispatch unleased or expired items from active durable runs."""

    roots = dispatch_prescreen_batch_roots(limit=limit)
    ambiguous = reap_ambiguous_prescreen_attempts(limit=limit)
    result = dispatch_prescreen_queued_items(limit=limit)
    result.update({
        "root_claimed": roots["claimed"], "root_enqueued": roots["enqueued"],
        "root_dispatch_errors": roots["dispatch_errors"],
    })
    result["ambiguous"] = ambiguous["ambiguous"]
    result["ambiguous_run_ids"] = ambiguous["run_ids"]
    if result["claimed"] or result["dispatch_errors"] or result["ambiguous"] or result["root_claimed"]:
        logger.info("pre-screen dispatch recovery result=%s", result)
    return result


@celery_app.task(
    name="app.tasks.prescreen_tasks.pre_screen_application_job",
    queue="scoring",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=240,
    time_limit=270,
)
def pre_screen_application_job(item_id: int, *, refresh: bool = False) -> dict:
    """Run one paid attempt, surfacing any crash-window outcome as ambiguous.

    ``attempting`` is committed before resolving/calling the provider, so no DB
    lock or connection is held across network I/O. Duplicate deliveries see
    that state and do not call. If this worker disappears after a paid response
    but before committing the application, Beat terminally marks the attempt
    ambiguous instead of automatically buying another call.
    """

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import (
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
        CvScoreJob,
    )
    from ..models.organization import Organization
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_AMBIGUOUS,
        PRESCREEN_BATCH_ITEM_ATTEMPTING,
        PRESCREEN_BATCH_ITEM_DONE,
        PRESCREEN_BATCH_ITEM_ERROR,
        PRESCREEN_BATCH_ITEM_QUEUED,
        PRESCREEN_BATCH_ITEM_SKIPPED,
        PrescreenBatchItem,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_client_for_org
    from ..services.pre_screening_service import (
        application_needs_pre_screen,
        execute_pre_screen_only,
    )

    run_id: int | None = None
    application_id: int | None = None
    attempt_token: str | None = None
    dispatch_auto_reject = False
    outcome = "error"

    # Phase 1: validate/skip or durably reserve the paid attempt, then release
    # the transaction before any provider work.
    db = SessionLocal()
    try:
        item = (
            db.query(PrescreenBatchItem)
            .filter(PrescreenBatchItem.id == int(item_id))
            .with_for_update()
            .one_or_none()
        )
        if item is None:
            return {"status": "missing_item", "item_id": int(item_id)}
        run_id = int(item.run_id)
        application_id = int(item.application_id)
        if item.status != PRESCREEN_BATCH_ITEM_QUEUED:
            return {
                "status": (
                    "attempt_in_progress"
                    if item.status == PRESCREEN_BATCH_ITEM_ATTEMPTING
                    else "already_terminal"
                ),
                "item_id": int(item_id),
                "item_status": item.status,
            }
        run, durable_refresh, parent_error = _prescreen_item_run_authority(db, item)
        if parent_error is not None:
            item.status = PRESCREEN_BATCH_ITEM_SKIPPED
            item.error_code = parent_error
            item.finished_at = datetime.now(timezone.utc)
            item.dispatch_token = None
            item.dispatch_lease_until = None
            db.commit()
            return {"status": "skipped", "item_id": int(item_id), "application_id": application_id}
        if bool(refresh) != durable_refresh:
            logger.warning("pre-screen item ignored stale refresh item_id=%s", item_id)
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.id == application_id,
                CandidateApplication.organization_id == int(item.organization_id),
                CandidateApplication.role_id == int(item.role_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .with_for_update(of=CandidateApplication)
            .one_or_none()
        )
        org = db.query(Organization).filter(
            Organization.id == int(item.organization_id)
        ).one_or_none()
        active_score = db.query(CvScoreJob.id).filter(
            CvScoreJob.application_id == application_id,
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
        ).first()
        conflict_code = _prescreen_item_conflict_code(db, item, run)
        if app is None or org is None:
            item.status = PRESCREEN_BATCH_ITEM_SKIPPED
            item.error_code = "target_missing"
            outcome = "skipped"
        elif conflict_code is not None:
            item.status = PRESCREEN_BATCH_ITEM_SKIPPED
            item.error_code = conflict_code
            outcome = "skipped"
        elif active_score is not None:
            # The canonical score worker will run the same gate. Avoid paying
            # twice when a manual pre-screen overlaps an existing score job.
            item.status = PRESCREEN_BATCH_ITEM_SKIPPED
            item.error_code = "score_job_active"
            outcome = "skipped"
        elif not durable_refresh and not application_needs_pre_screen(app):
            item.status = PRESCREEN_BATCH_ITEM_SKIPPED
            item.error_code = "already_current"
            outcome = "skipped"
        else:
            attempt_token = str(uuid4())
            item.status = PRESCREEN_BATCH_ITEM_ATTEMPTING
            item.provider_attempt_token = attempt_token
            item.provider_attempt_started_at = datetime.now(timezone.utc)
            item.error_code = None
            outcome = "attempting"
        item.dispatch_token = None
        item.dispatch_lease_until = None
        if outcome == "skipped":
            item.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("pre-screen item preparation failed item_id=%s error_type=%s", item_id, type(exc).__name__)
        return {"status": "error", "item_id": int(item_id)}
    finally:
        db.close()

    if outcome == "skipped" or attempt_token is None:
        if run_id is not None:
            _refresh_run_progress(run_id)
        return {
            "status": outcome,
            "item_id": int(item_id),
            "application_id": application_id,
        }

    # Phase 2: the paid call and application mutation share a transaction, but
    # the attempt marker above does not. A crash rolls back candidate state
    # while leaving ``attempting`` for conservative ambiguity recovery.
    db = SessionLocal()
    try:
        live_item = db.query(PrescreenBatchItem).filter(
            PrescreenBatchItem.id == int(item_id),
            PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
            PrescreenBatchItem.provider_attempt_token == attempt_token,
        ).one_or_none()
        if live_item is None:
            return {"status": "attempt_not_owned", "item_id": int(item_id)}
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id
                == int(live_item.organization_id),
                CandidateApplication.role_id == int(live_item.role_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .one_or_none()
        )
        org = db.query(Organization).filter(
            Organization.id == int(live_item.organization_id)
        ).one_or_none()
        if app is None or org is None:
            result = {"status": "skipped"}
            terminal_status = PRESCREEN_BATCH_ITEM_SKIPPED
            terminal_error = "target_missing"
            outcome = "skipped"
        else:
            result = execute_pre_screen_only(
                app,
                db=db,
                client=get_client_for_org(org),
            )
            if result.get("status") == "error":
                terminal_status = PRESCREEN_BATCH_ITEM_ERROR
                terminal_error = "pre_screen_error"
                outcome = "error"
            else:
                terminal_status = PRESCREEN_BATCH_ITEM_DONE
                terminal_error = None
                outcome = "done"
                dispatch_auto_reject = True
        with db.no_autoflush:
            terminal_item = (
                db.query(PrescreenBatchItem)
                .filter(
                    PrescreenBatchItem.id == int(item_id),
                    PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
                    PrescreenBatchItem.provider_attempt_token == attempt_token,
                )
                .with_for_update()
                .one_or_none()
            )
        if terminal_item is None:
            db.rollback()
            outcome = "ambiguous"
            dispatch_auto_reject = False
        else:
            terminal_item.status = terminal_status
            terminal_item.error_code = terminal_error
            terminal_item.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("pre-screen paid attempt failed item_id=%s error_type=%s", item_id, type(exc).__name__)
        outcome = "ambiguous"
        dispatch_auto_reject = False
        ambiguity_db = SessionLocal()
        try:
            updated = (
                ambiguity_db.query(PrescreenBatchItem)
                .filter(
                    PrescreenBatchItem.id == int(item_id),
                    PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
                    PrescreenBatchItem.provider_attempt_token == attempt_token,
                )
                .update(
                    {
                        PrescreenBatchItem.status: PRESCREEN_BATCH_ITEM_AMBIGUOUS,
                        PrescreenBatchItem.error_code:
                            "provider_attempt_outcome_unknown",
                        PrescreenBatchItem.finished_at: datetime.now(timezone.utc),
                    },
                    synchronize_session=False,
                )
            )
            ambiguity_db.commit()
            if not updated:
                outcome = "attempt_not_owned"
        except Exception as exc:
            ambiguity_db.rollback()
            logger.error("pre-screen ambiguity marker failed item_id=%s error_type=%s", item_id, type(exc).__name__)
        finally:
            ambiguity_db.close()
    finally:
        db.close()

    if dispatch_auto_reject and application_id is not None:
        try:
            from .automation_tasks import run_application_auto_reject

            run_application_auto_reject.delay(application_id)
        except Exception as exc:
            # Pre-screen state is durable; the standing reject sweep is the
            # recovery net for a transient follow-up dispatch failure.
            logger.error("post-pre-screen reject dispatch failed application_id=%s error_type=%s", application_id, type(exc).__name__)
    if run_id is not None:
        _refresh_run_progress(run_id)
    return {
        "status": outcome,
        "item_id": int(item_id),
        "application_id": application_id,
    }


@celery_app.task(
    name="app.tasks.prescreen_tasks.process_role_job",
    queue="scoring",
)
def process_role_job(
    run_id: int,
    organization_id: int | None = None,
    **kwargs,
) -> dict:
    """Claim and execute one durable multi-step Process cascade.

    ``organization_id`` remains optional only so broker messages emitted by a
    pre-durability deployment can drain safely during a rolling deploy.
    """

    from ..domains.assessments_runtime.applications_routes import _run_process

    if organization_id is not None:
        progress = _run_process(int(run_id), int(organization_id), **kwargs)
        return {
            "status": str(progress.get("status") or "completed"),
            "role_id": int(run_id),
            "legacy_delivery": True,
        }

    from ..platform.database import SessionLocal
    from ..services.process_role_dispatch import claim_process_worker, fail_process_run

    with SessionLocal() as db:
        try:
            claim = claim_process_worker(db, run_id=int(run_id))
            db.commit()
        except Exception:
            db.rollback()
            if fail_process_run(
                db,
                run_id=int(run_id),
                error_code="process_dispatch_invalid",
            ):
                db.commit()
            raise

    # Redis stays a display cache; the claim above is the execution authority.
    from ..domains.assessments_runtime.applications_routes import _set_process_progress

    _set_process_progress(int(claim.role_id), claim.progress)
    if claim.state != "claimed" or claim.payload is None:
        return {
            "status": claim.state,
            "run_id": int(claim.run_id),
            "role_id": int(claim.role_id),
        }

    payload = dict(claim.payload)
    payload.pop("role_id", None)
    payload.pop("organization_id", None)
    progress = _run_process(
        int(claim.role_id),
        int(claim.organization_id),
        run_id=int(claim.run_id),
        **payload,
    )
    return {
        "status": str(progress.get("status") or "completed"),
        "run_id": int(claim.run_id),
        "role_id": int(claim.role_id),
    }


@celery_app.task(name="app.tasks.prescreen_tasks.recover_process_role_runs")
def recover_process_role_runs(limit: int = 100) -> dict:
    """Recover broker loss and surface expired Process workers safely."""

    from ..models.background_job_run import JOB_KIND_PROCESS_ROLE, BackgroundJobRun
    from ..platform.database import SessionLocal
    from ..services.process_role_dispatch import (
        PROCESS_PUBLISHABLE_STATUSES,
        claim_process_publish,
        expire_stale_process_workers,
        mark_process_dispatched,
        process_publish_due_filter,
    )

    bound = max(1, min(int(limit), 500))
    with SessionLocal() as db:
        expired = expire_stale_process_workers(db, limit=bound)
        rows = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
                BackgroundJobRun.status.in_(PROCESS_PUBLISHABLE_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
                process_publish_due_filter(),
            )
            .order_by(BackgroundJobRun.id.asc())
            .limit(bound)
            .with_for_update(skip_locked=True)
            .all()
        )
        payloads = [
            payload
            for row in rows
            if (payload := claim_process_publish(row)) is not None
        ]
        db.commit()

    from ..domains.assessments_runtime.applications_routes import _set_process_progress

    for role_id, progress in expired:
        _set_process_progress(int(role_id), progress)

    kicked = publish_failed = 0
    for payload in payloads:
        try:
            process_role_job.delay(**payload)
            kicked += 1
            with SessionLocal() as receipt_db:
                mark_process_dispatched(
                    receipt_db,
                    run_id=int(payload["run_id"]),
                )
                receipt_db.commit()
        except Exception as exc:
            publish_failed += 1
            logger.error("Process recovery publish failed run_id=%s error_type=%s", payload["run_id"], type(exc).__name__)
    return {
        "scanned": len(rows),
        "kicked": kicked,
        "publish_failed": publish_failed,
        "expired_workers": len(expired),
    }


__all__ = [
    "batch_pre_screen_role_job",
    "dispatch_prescreen_queued_items",
    "pre_screen_application_job",
    "process_role_job",
    "reap_ambiguous_prescreen_attempts",
    "recover_process_role_runs",
    "recover_prescreen_batch_dispatches",
    "select_prescreen_target_ids",
]
