"""Durable, cache-backed talent-pool re-score workers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.pool_rescore")

_LEASE = timedelta(minutes=10)
_MAX_START_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _retry_at(attempts: int) -> datetime:
    return _now() + timedelta(minutes=2 ** min(max(int(attempts), 1), 6))


def _claim(db, job_id: int):
    from ..models.pool_rescore_job import (
        POOL_RESCORE_PENDING,
        POOL_RESCORE_RUNNING,
        PoolRescoreJob,
    )

    now = _now()
    job = (
        db.query(PoolRescoreJob)
        .filter(PoolRescoreJob.id == int(job_id))
        .with_for_update()
        .one_or_none()
    )
    if job is None:
        return None, "missing"
    next_attempt = _as_utc(job.next_attempt_at)
    lease_until = _as_utc(job.lease_until)
    due = next_attempt is None or next_attempt <= now
    expired = lease_until is None or lease_until <= now
    if job.status == POOL_RESCORE_PENDING and due:
        pass
    elif job.status == POOL_RESCORE_RUNNING and expired:
        pass
    else:
        return job, "busy_or_complete"
    job.status = POOL_RESCORE_RUNNING
    job.attempts = int(job.attempts or 0) + 1
    job.started_at = job.started_at or now
    job.lease_until = now + _LEASE
    job.next_attempt_at = None
    job.error_message = None
    db.commit()
    db.refresh(job)
    return job, "claimed"


def _record_start_failure(db, job, exc: Exception) -> dict:
    from ..models.pool_rescore_job import POOL_RESCORE_ERROR, POOL_RESCORE_PENDING

    job.lease_until = None
    job.error_message = "score provider unavailable"
    if int(job.attempts or 0) >= _MAX_START_ATTEMPTS:
        job.status = POOL_RESCORE_ERROR
        job.finished_at = _now()
    else:
        job.status = POOL_RESCORE_PENDING
        job.next_attempt_at = _retry_at(int(job.attempts or 1))
    db.commit()
    logger.warning(
        "pool re-score start failed job=%s attempt=%s type=%s",
        job.id,
        job.attempts,
        type(exc).__name__,
    )
    return {"ok": False, "error": "client_init_failed", "status": job.status}


@celery_app.task(name="rescore_pool_against_requirement")
def rescore_pool_against_requirement(job_id: int) -> dict:
    """Claim and drain one job, committing a receipt after every candidate.

    The holistic cache is written before this function receives a result; the
    per-candidate result is then committed immediately. A worker redelivery
    skips those receipts, so completed paid work is not repeated.
    """
    from ..cv_matching.holistic import run_holistic_match
    from ..models.candidate_application import CandidateApplication
    from ..models.pool_rescore_job import (
        POOL_RESCORE_DONE,
        POOL_RESCORE_RUNNING,
        PoolRescoreJob,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client

    with SessionLocal() as db:
        job, claim = _claim(db, int(job_id))
        if job is None:
            logger.warning("pool re-score job %s not found", job_id)
            return {"ok": False, "error": "job_not_found"}
        if claim != "claimed":
            return {"ok": True, "status": job.status, "skipped": True}

        job_pk = int(job.id)
        expected_attempt = int(job.attempts or 0)
        requirement = str(job.requirement_text or "")
        org_id = int(job.organization_id)
        app_ids = [int(x) for x in (job.application_ids or [])]
        completed = {
            int(row["application_id"]): dict(row)
            for row in (job.results or [])
            if isinstance(row, dict) and row.get("application_id") is not None
        }
        db.rollback()
        try:
            client = get_metered_client(organization_id=org_id)
        except Exception as exc:  # noqa: BLE001
            job = (
                db.query(PoolRescoreJob)
                .filter(
                    PoolRescoreJob.id == job_pk,
                    PoolRescoreJob.status == POOL_RESCORE_RUNNING,
                    PoolRescoreJob.attempts == expected_attempt,
                )
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                db.rollback()
                return {"ok": True, "status": "superseded", "skipped": True}
            return _record_start_failure(db, job, exc)

        try:
            from ..services.workable_context_service import format_workable_context
        except Exception:  # noqa: BLE001 - notes are best-effort
            format_workable_context = None  # type: ignore[assignment]

        apps = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id.in_([x for x in app_ids if x not in completed]),
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
            if app_ids
            else []
        )
        found_ids = {int(app.id) for app in apps}
        for missing_id in app_ids:
            if missing_id not in completed and missing_id not in found_ids:
                completed[missing_id] = {
                    "application_id": missing_id,
                    "role_fit_score": None,
                    "summary": None,
                    "scoring_status": "not_found",
                    "cache_hit": False,
                }

        work_items: list[tuple[int, str, str | None]] = []
        for app in apps:
            app_id = int(app.id)
            cv = app.cv_text or (
                app.candidate.cv_text if app.candidate is not None else None
            )
            workable_context = None
            if format_workable_context is not None:
                try:
                    workable_context = format_workable_context(app.candidate, app) or None
                except Exception:  # noqa: BLE001
                    workable_context = None
            work_items.append((app_id, str(cv or ""), workable_context))
        db.rollback()

        for app_id, cv_text, workable_context in work_items:
            try:
                out = run_holistic_match(
                    cv_text,
                    requirement,
                    client=client,
                    metering_context={
                        "organization_id": org_id,
                        "role_id": None,
                        "entity_id": f"application:{app_id}",
                    },
                    workable_context=workable_context,
                )
                status = getattr(out.scoring_status, "value", str(out.scoring_status))
                ok = str(status).lower() == "ok"
                completed[app_id] = {
                    "application_id": app_id,
                    "role_fit_score": out.role_fit_score if ok else None,
                    "summary": (out.summary or "")[:1000] if ok else None,
                    "scoring_status": status,
                    "cache_hit": bool(getattr(out, "cache_hit", False)),
                }
            except Exception:  # noqa: BLE001 - degrade one app, preserve batch
                logger.exception("pool re-score app=%s failed", app_id)
                completed[app_id] = {
                    "application_id": app_id,
                    "role_fit_score": None,
                    "summary": None,
                    "scoring_status": "failed",
                    "cache_hit": False,
                }
            # Receipt + lease heartbeat after every app. A killed worker can
            # restart from the first missing id rather than replaying the batch.
            job = (
                db.query(PoolRescoreJob)
                .filter(
                    PoolRescoreJob.id == job_pk,
                    PoolRescoreJob.status == POOL_RESCORE_RUNNING,
                    PoolRescoreJob.attempts == expected_attempt,
                )
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                db.rollback()
                return {"ok": True, "status": "superseded", "skipped": True}
            job.results = list(completed.values())
            job.lease_until = _now() + _LEASE
            db.commit()

        results = list(completed.values())
        results.sort(
            key=lambda row: (
                row["role_fit_score"]
                if row.get("role_fit_score") is not None
                else float("-inf")
            ),
            reverse=True,
        )
        scored = sum(1 for row in results if str(row.get("scoring_status")).lower() == "ok")
        cached = sum(1 for row in results if row.get("cache_hit"))
        failed = len(results) - scored
        job = (
            db.query(PoolRescoreJob)
            .filter(
                PoolRescoreJob.id == job_pk,
                PoolRescoreJob.status == POOL_RESCORE_RUNNING,
                PoolRescoreJob.attempts == expected_attempt,
            )
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            db.rollback()
            return {"ok": True, "status": "superseded", "skipped": True}
        job.results = results
        job.counts = {
            "requested": len(app_ids),
            "scored": scored,
            "cached": cached,
            "failed": failed,
        }
        job.status = POOL_RESCORE_DONE
        job.finished_at = _now()
        job.lease_until = None
        job.next_attempt_at = None
        job.error_message = None
        db.commit()
        return {"ok": True, "scored": scored, "cached": cached, "failed": failed}


@celery_app.task(name="app.tasks.pool_rescore_tasks.recover_pool_rescore_jobs")
def recover_pool_rescore_jobs(limit: int = 100) -> dict:
    """Bounded Beat recovery for lost publishes and expired worker leases."""
    from ..models.pool_rescore_job import (
        POOL_RESCORE_PENDING,
        POOL_RESCORE_RUNNING,
        PoolRescoreJob,
    )
    from ..platform.database import SessionLocal

    now = _now()
    with SessionLocal() as db:
        stale = (
            db.query(PoolRescoreJob)
            .filter(
                PoolRescoreJob.status == POOL_RESCORE_RUNNING,
                (
                    PoolRescoreJob.lease_until.is_(None)
                    | (PoolRescoreJob.lease_until < now)
                ),
            )
            .limit(max(1, int(limit)))
            .all()
        )
        for job in stale:
            job.status = POOL_RESCORE_PENDING
            job.lease_until = None
            job.next_attempt_at = now
            job.error_message = "worker_interrupted"
        db.commit()
        ids = [
            int(row[0])
            for row in (
                db.query(PoolRescoreJob.id)
                .filter(
                    PoolRescoreJob.status == POOL_RESCORE_PENDING,
                    (
                        PoolRescoreJob.next_attempt_at.is_(None)
                        | (PoolRescoreJob.next_attempt_at <= now)
                    ),
                )
                .order_by(PoolRescoreJob.id.asc())
                .limit(max(1, int(limit)))
                .all()
            )
        ]

    kicked = publish_failed = 0
    for pending_id in ids:
        try:
            rescore_pool_against_requirement.delay(pending_id)
            kicked += 1
        except Exception:
            publish_failed += 1
            logger.exception("pool re-score recovery publish failed job=%s", pending_id)
    return {
        "scanned": len(ids),
        "stale_recovered": len(stale),
        "kicked": kicked,
        "publish_failed": publish_failed,
    }
