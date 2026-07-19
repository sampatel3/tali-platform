"""Resumable implementation for the scoring-batch Celery root task."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import secrets
from typing import Iterable

from sqlalchemy.orm import joinedload

from .celery_app import celery_app
from .scoring_batch_run import (
    ScoringBatchLeaseLost,
    ScoringBatchProgress,
    claim_scoring_batch_run,
)


logger = logging.getLogger(__name__)
_QUERY_CHUNK_SIZE = 500
_HEARTBEAT_ITEMS = 10


def _delivery_id() -> str:
    task = getattr(celery_app, "current_task", None)
    request = getattr(task, "request", None)
    value = getattr(request, "id", None)
    return str(value or f"local-{secrets.token_hex(16)}")


def _chunks(values: list[int]) -> Iterable[list[int]]:
    for offset in range(0, len(values), _QUERY_CHUNK_SIZE):
        yield values[offset : offset + _QUERY_CHUNK_SIZE]


def _legacy_cancel_requested(role_id: int) -> bool:
    try:
        from ..domains.assessments_runtime.applications_routes import (
            is_batch_score_cancelled,
        )

        return bool(is_batch_score_cancelled(int(role_id)))
    except Exception:  # pragma: no cover - legacy Redis is best effort
        return False


def _cancel_requested(progress, db, run) -> bool:
    return (
        progress.cancel_requested(db, run)
        if run is not None
        else _legacy_cancel_requested(progress.role_id)
    )


def _scoped_applications(
    db,
    *,
    role,
    target_ids: list[int] | None,
    include_scored: bool,
    applied_after: str | None,
):
    from ..models.candidate import Candidate
    from ..models.candidate_application import CandidateApplication

    def base_query():
        return (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.role_id == int(role.id),
                CandidateApplication.organization_id == int(role.organization_id),
                CandidateApplication.deleted_at.is_(None),
            )
        )

    if target_ids is not None:
        by_id = {}
        for chunk in _chunks(target_ids):
            for application in (
                base_query().filter(CandidateApplication.id.in_(chunk)).all()
            ):
                by_id[int(application.id)] = application
        return [
            by_id[application_id]
            for application_id in target_ids
            if application_id in by_id
        ]

    query = base_query()
    if not include_scored:
        query = query.filter(CandidateApplication.cv_match_score.is_(None))
    if applied_after:
        cutoff = datetime.fromisoformat(applied_after)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        query = query.join(
            Candidate,
            CandidateApplication.candidate_id == Candidate.id,
        ).filter(Candidate.workable_created_at >= cutoff)
    return query.order_by(CandidateApplication.id).all()


def _schedule_busy_redelivery(
    *,
    role_id: int,
    include_scored: bool,
    applied_after: str | None,
    run_id: int,
    retry_after_seconds: int,
) -> None:
    if bool(celery_app.conf.task_always_eager):
        return
    try:
        from .scoring_tasks import batch_score_role

        batch_score_role.apply_async(
            args=(int(role_id),),
            kwargs={
                "include_scored": bool(include_scored),
                "applied_after": applied_after,
                "run_id": int(run_id),
            },
            countdown=max(1, int(retry_after_seconds)),
        )
    except Exception:
        logger.exception(
            "failed to schedule scoring fanout lease retry run_id=%s", run_id
        )


def _cancel_owned_work(db, run_id: int | None) -> None:
    if run_id is None:
        return
    from ..services.score_job_dispatch import cancel_pending_batch_score_jobs

    cancel_pending_batch_score_jobs(db, batch_run_id=int(run_id))


def _cancel_batch(db, progress, run, phase: str) -> dict:
    """Cancel owned pending work and terminalize only after the batch drains."""

    _cancel_owned_work(db, progress.run_id)
    if run is None:
        return progress.cancelled_result(db, run, phase)
    from ..models.cv_score_job import (
        CvScoreJob,
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
    )

    active = (
        db.query(CvScoreJob.id)
        .filter(
            CvScoreJob.batch_run_id == int(run.id),
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
        )
        .first()
        is not None
    )
    if not active:
        external_ids = sorted(progress.score_job_ids - progress.owned_score_job_ids)
        for chunk in _chunks(external_ids):
            if (
                db.query(CvScoreJob.id)
                .filter(
                    CvScoreJob.id.in_(chunk),
                    CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
                )
                .first()
                is not None
            ):
                active = True
                break
    if active:
        return progress.cancelled_result(db, run, phase)
    progress.save(
        db,
        run,
        f"cancelled_{phase}",
        status="cancelled",
        finished=True,
        cancelled=True,
    )
    return progress.result(status="cancelled")


def run_scoring_batch(
    role_id: int,
    *,
    include_scored: bool,
    applied_after: str | None,
    run_id: int | None,
) -> dict:
    """Resume exact targets and persist a receipt after every paid dispatch."""

    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score
    from ..services.score_job_dispatch import ensure_score_job_published

    db = SessionLocal()
    durable_run = None
    delivery_id = _delivery_id()
    progress = ScoringBatchProgress(
        int(role_id),
        run_id,
        bool(include_scored),
        applied_after,
        owner_delivery_id=delivery_id,
    )
    try:
        role = db.query(Role).filter(Role.id == int(role_id)).first()
        durable_run, early_result = claim_scoring_batch_run(
            db,
            run_id=run_id,
            role_id=int(role_id),
            organization_id=(int(role.organization_id) if role is not None else None),
            delivery_id=delivery_id,
            # A missing role is terminal before any target can be consumed.
            # Preserve that stable outcome while canonicalizing its inert audit
            # receipt; live roles still require a byte-for-byte exact cohort.
            require_exact_target_snapshot=role is not None,
        )
        if durable_run is not None:
            progress.owner_delivery_id = str(
                durable_run.counters.get("fanout_owner_delivery_id") or ""
            )
            progress.adopt_total(durable_run)
            recovered_jobs = progress.recover_owned_receipts(db, durable_run)
        else:
            recovered_jobs = []
        if early_result is not None:
            if early_result.get("status") == "delivery_busy" and run_id is not None:
                _schedule_busy_redelivery(
                    role_id=role_id,
                    include_scored=include_scored,
                    applied_after=applied_after,
                    run_id=run_id,
                    retry_after_seconds=int(
                        early_result.get("retry_after_seconds") or 1
                    ),
                )
            if early_result.get("status") == "cancelled":
                if durable_run is not None:
                    return _cancel_batch(db, progress, durable_run, "before_fetch")
            return early_result
        if role is None:
            if durable_run is not None:
                progress.save(
                    db,
                    durable_run,
                    "failed_before_fetch",
                    status="failed",
                    finished=True,
                    error="scoring_batch_role_missing",
                    fanout_failed=True,
                )
            return {"status": "missing_role", "role_id": int(role_id)}

        # A durable target snapshot is already the authoritative selection, so
        # stale/corrupt filter metadata must neither narrow nor kill it. Legacy
        # dynamic deliveries still validate before any broker/provider work.
        if applied_after and durable_run is None:
            datetime.fromisoformat(applied_after)

        # A crash after row commit but before broker-receipt persistence is
        # ambiguous. Republishing that pinned row is provider-safe.
        for job in recovered_jobs:
            ensure_score_job_published(db, job)

        org = (
            db.query(Organization)
            .filter(Organization.id == int(role.organization_id))
            .first()
        )
        exact_targets = (
            list(progress.target_application_ids) if durable_run is not None else None
        )
        apps = _scoped_applications(
            db,
            role=role,
            target_ids=exact_targets,
            include_scored=include_scored,
            applied_after=applied_after,
        )
        progress.selected = len(apps)
        if exact_targets is None:
            progress.add_targets(app.id for app in apps)
        progress.total = max(progress.total, len(progress.target_application_ids))
        progress.excluded_by_filter = max(0, progress.total - progress.selected)
        progress.save(db, durable_run, "fetching")

        try:
            from ..domains.assessments_runtime.applications_routes import (
                _try_fetch_cv_from_workable,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Workable CV helper import failed error_type=%s",
                type(exc).__name__,
            )
            _try_fetch_cv_from_workable = None  # type: ignore[assignment]

        for index, app in enumerate(apps, start=1):
            if _cancel_requested(progress, db, durable_run):
                return _cancel_batch(db, progress, durable_run, "fetch")
            if (app.cv_text or "").strip():
                if index % _HEARTBEAT_ITEMS == 0:
                    progress.save(db, durable_run, "fetching")
                continue
            try:
                if app.candidate and (app.candidate.cv_text or "").strip():
                    app.cv_file_url = app.candidate.cv_file_url
                    app.cv_filename = app.candidate.cv_filename
                    app.cv_text = app.candidate.cv_text
                    app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    progress.fetched += 1
                elif (
                    (app.source or "") == "workable"
                    and org is not None
                    and _try_fetch_cv_from_workable is not None
                ):
                    # Every fetch may cross several bounded provider calls. Renew
                    # immediately before it so time spent on earlier candidates
                    # cannot let recovery steal a still-live delivery.
                    if durable_run is not None:
                        progress.save(db, durable_run, "fetching")
                    if _try_fetch_cv_from_workable(app, app.candidate, db, org):
                        progress.fetched += 1
                    else:
                        progress.fetch_failures += 1
            except ScoringBatchLeaseLost:
                raise
            except Exception:
                logger.exception("Batch CV fetch failed application_id=%s", app.id)
                progress.fetch_failures += 1
            if index % _HEARTBEAT_ITEMS == 0:
                progress.save(db, durable_run, "fetching")
        progress.commit_fetches(db, durable_run)

        apps = _scoped_applications(
            db,
            role=role,
            target_ids=exact_targets,
            include_scored=include_scored,
            applied_after=applied_after,
        )
        progress.missing_cv = sum(1 for app in apps if not (app.cv_text or "").strip())
        progress.save(db, durable_run, "enqueuing")

        for index, app in enumerate(apps, start=1):
            if _cancel_requested(progress, db, durable_run):
                return _cancel_batch(db, progress, durable_run, "enqueue")
            if int(app.id) in progress.score_job_application_ids:
                continue
            if not (app.cv_text or "").strip():
                continue
            job = enqueue_score(
                db,
                app,
                force=False,
                requires_active_agent=False,
                batch_run_id=(int(run_id) if run_id is not None else None),
                batch_delivery_id=(
                    progress.owner_delivery_id if run_id is not None else None
                ),
            )
            if job is not None:
                progress.record_enqueued(int(app.id), job)
                if str(getattr(job, "cache_hit", "") or "") == "pre_screen_filtered":
                    progress.pre_screened_out += 1
            else:
                progress.enqueue_skipped += 1
            # Owned rows are the durable receipt and are reconstructed after a
            # crash. Reused external jobs exist outside that ownership, so save
            # their exact ID immediately; otherwise checkpoint in bounded
            # batches to avoid rewriting growing JSON arrays for every target.
            owned = (
                job is not None
                and progress.run_id is not None
                and getattr(job, "batch_run_id", None) == progress.run_id
            )
            if (job is not None and not owned) or index % _HEARTBEAT_ITEMS == 0:
                progress.save(db, durable_run, "enqueuing")

        if _cancel_requested(progress, db, durable_run):
            return _cancel_batch(db, progress, durable_run, "after_enqueue")
        if durable_run is None:
            try:
                from ..domains.assessments_runtime.applications_routes import (
                    _BATCH_SCORE_CANCEL_PREFIX,
                    _clear_cancel_flag,
                )

                _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, int(role_id))
            except Exception:
                pass
        return progress.finalize(db, durable_run)
    except Exception:
        db.rollback()
        if durable_run is not None:
            try:
                progress.fail(db, durable_run)
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to persist scoring batch failure run_id=%s", run_id
                )
        raise
    finally:
        db.close()


__all__ = ["run_scoring_batch"]
