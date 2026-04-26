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


@celery_app.task(name="app.tasks.scoring_tasks.score_application_job", bind=True, max_retries=0)
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


@celery_app.task(name="app.tasks.scoring_tasks.batch_score_role")
def batch_score_role(role_id: int, *, include_scored: bool = False) -> dict:
    """Fan out per-application scoring jobs for every application under a role.

    Replaces the legacy ``threading.Thread`` batch loop in applications_routes.
    Each application gets its own ``score_application_job``, queued in
    parallel rather than processed serially in a single thread.
    """
    from ..models.candidate_application import CandidateApplication
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "missing_role", "role_id": role_id}

        query = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if not include_scored:
            query = query.filter(CandidateApplication.cv_match_score.is_(None))

        enqueued = 0
        for app in query.all():
            job = enqueue_score(db, app, force=include_scored)
            if job is not None:
                enqueued += 1
        db.commit()
        return {"status": "enqueued", "role_id": role_id, "count": enqueued}
    finally:
        db.close()
