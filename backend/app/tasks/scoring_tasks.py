"""Celery task for asynchronous CV scoring.

The body delegates to ``cv_score_orchestrator._execute_scoring`` so the
inline (Celery-disabled) and async paths share the same code. The task
opens its own database session because Celery workers run in a separate
process from the FastAPI request handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.scoring_tasks.score_application_job",
    bind=True,
    max_retries=0,
    queue="scoring",
)
def score_application_job(self, application_id: int) -> dict:
    """Score a single application asynchronously.

    The orchestrator wires the cache + Claude call + result persistence;
    this task is just the worker shell. Retries are disabled here because
    a transient Claude failure should mark the latest job as ``error`` and
    let the recruiter trigger a manual rescore — silent retries would mask
    real issues like a malformed prompt.
    """
    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import CvScoreJob, SCORE_JOB_ERROR
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import _execute_scoring, _latest_job

    db = SessionLocal()
    try:
        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if application is None:
            logger.warning("score_application_job: application_id=%s not found", application_id)
            return {"status": "missing", "application_id": application_id}

        job = _latest_job(db, application_id)
        if job is None:
            logger.warning(
                "score_application_job: no CvScoreJob row for application_id=%s",
                application_id,
            )
            return {"status": "no_job", "application_id": application_id}

        if job.status not in {"pending", "stale"}:
            # Another worker already picked this up — bail out.
            return {"status": "skipped", "application_id": application_id, "job_status": job.status}

        try:
            _execute_scoring(db, application=application, job=job)
            db.commit()
            return {
                "status": job.status,
                "application_id": application_id,
                "cache_hit": job.cache_hit,
            }
        except Exception as exc:
            logger.exception("score_application_job failed for application_id=%s", application_id)
            db.rollback()
            try:
                refreshed_job = (
                    db.query(CvScoreJob).filter(CvScoreJob.id == job.id).first()
                )
                if refreshed_job is not None:
                    refreshed_job.status = SCORE_JOB_ERROR
                    refreshed_job.error_message = f"task_exception: {exc}"
                    refreshed_job.finished_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                db.rollback()
            return {"status": "error", "application_id": application_id, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.scoring_tasks.batch_score_role",
    queue="scoring",
)
def batch_score_role(role_id: int, *, include_scored: bool = False) -> dict:
    """Fan out per-application scoring jobs for every application under a role.

    For Workable-imported applications missing ``cv_text``, the CV is fetched
    from Workable inline before per-app score tasks are dispatched. Without
    this, ``enqueue_score`` returns None for missing-CV apps and they're
    silently dropped from the batch — which is exactly what was happening
    in production before this fix (counted 1/600 because only 1 app had a
    CV pre-fetched).

    The fetch is sequential (~3-5s per Workable candidate). Per-app
    scoring then fans out to parallel ``score_application_job`` tasks. For
    600 candidates the fetch loop takes ~30-50 min; scoring runs in the
    background after that.
    """
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "missing_role", "role_id": role_id}

        org = (
            db.query(Organization)
            .filter(Organization.id == role.organization_id)
            .first()
        )

        query = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if not include_scored:
            query = query.filter(CandidateApplication.cv_match_score.is_(None))

        apps = query.all()

        # 1. Fetch missing CVs (Workable apps + candidate-level fallback).
        # Lazy import to avoid circular dependency: applications_routes
        # imports services, so we can't import it at module load.
        try:
            from ..domains.assessments_runtime.applications_routes import (
                _try_fetch_cv_from_workable,
                is_batch_score_cancelled,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("Failed to import _try_fetch_cv_from_workable: %s", exc)
            _try_fetch_cv_from_workable = None  # type: ignore[assignment]
            is_batch_score_cancelled = lambda _: False  # type: ignore[assignment]

        fetched = 0
        fetch_failures = 0
        for app in apps:
            # Cooperative cancel between candidates so the recruiter
            # can stop a 600-candidate batch without restarting the worker.
            if is_batch_score_cancelled(role_id):
                logger.info(
                    "batch_score_role cancelled during fetch phase for role_id=%s",
                    role_id,
                )
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                return {
                    "status": "cancelled",
                    "role_id": role_id,
                    "count": 0,
                    "fetched": fetched,
                    "fetch_failures": fetch_failures,
                }
            if (app.cv_text or "").strip():
                continue
            try:
                # Candidate-level CV already extracted? Promote it.
                if app.candidate and (app.candidate.cv_text or "").strip():
                    app.cv_file_url = app.candidate.cv_file_url
                    app.cv_filename = app.candidate.cv_filename
                    app.cv_text = app.candidate.cv_text
                    app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    fetched += 1
                elif (
                    (app.source or "") == "workable"
                    and org is not None
                    and _try_fetch_cv_from_workable is not None
                ):
                    if _try_fetch_cv_from_workable(app, app.candidate, db, org):
                        fetched += 1
                    else:
                        fetch_failures += 1
            except Exception:
                logger.exception(
                    "Batch CV fetch failed for application_id=%s", app.id
                )
                fetch_failures += 1
        try:
            db.commit()
        except Exception:
            logger.exception("Failed to commit batch CV fetch results")
            db.rollback()

        # 2. Re-load apps so the freshly-set cv_text is visible. Not strictly
        # necessary since we kept the same session, but the commit may have
        # expired some attributes — explicit refresh is cheap.
        apps = query.all()

        enqueued = 0
        for app in apps:
            if is_batch_score_cancelled(role_id):
                logger.info(
                    "batch_score_role cancelled during enqueue phase for role_id=%s "
                    "(enqueued %d, remaining %d)",
                    role_id, enqueued, len(apps) - enqueued,
                )
                db.commit()
                return {
                    "status": "cancelled",
                    "role_id": role_id,
                    "count": enqueued,
                    "fetched": fetched,
                    "fetch_failures": fetch_failures,
                }
            job = enqueue_score(db, app, force=include_scored)
            if job is not None:
                enqueued += 1
        db.commit()

        # Clear the flag after a clean run so the next batch starts fresh.
        # If the run *was* cancelled we already early-returned above; in
        # both early-return cases the cancel endpoint clears the flag too.
        try:
            from ..domains.assessments_runtime.applications_routes import (
                _BATCH_SCORE_CANCEL_PREFIX,
                _clear_cancel_flag,
            )
            _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role_id)
        except Exception:
            pass

        return {
            "status": "enqueued",
            "role_id": role_id,
            "count": enqueued,
            "fetched": fetched,
            "fetch_failures": fetch_failures,
        }
    finally:
        db.close()
