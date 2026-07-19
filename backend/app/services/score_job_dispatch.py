"""Commit-before-publish dispatch for one durable CV score attempt."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import (
    CvScoreJob,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_STALE,
)


logger = logging.getLogger(__name__)


def create_and_dispatch_score_job(
    db: Session,
    *,
    application: CandidateApplication,
    requires_active_agent: bool,
    bypass_pre_screen: bool,
    batch_run_id: int | None,
) -> CvScoreJob:
    """Insert the visible attempt before publishing its pinned task message."""

    job = CvScoreJob(
        application_id=application.id,
        role_id=application.role_id,
        batch_run_id=batch_run_id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=bool(requires_active_agent),
        force_full_score=bool(bypass_pre_screen),
    )
    db.add(job)
    db.flush()

    from ..tasks.scoring_tasks import score_application_job

    # A worker on another connection must be able to resolve the pinned row.
    db.commit()
    try:
        async_result = score_application_job.delay(
            application.id,
            job_id=int(job.id),
            force_full_score=bypass_pre_screen,
        )
    except Exception:
        # Broker rejection must not leave a pending row that blocks recovery.
        job.status = SCORE_JOB_ERROR
        job.error_message = "broker_dispatch_failed"
        job.finished_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        logger.exception(
            "score dispatch failed application_id=%s job_id=%s",
            application.id,
            job.id,
        )
        raise
    job.celery_task_id = str(async_result.id)
    db.add(job)
    db.commit()
    return job


def ensure_score_job_published(db: Session, job: CvScoreJob) -> bool:
    """Republish an owned attempt missing its broker receipt.

    A process can die after the row commit and on either side of broker
    acceptance. Republishing is intentionally safe: the pinned score-job row
    has an atomic pending/stale -> running claim, so two messages cannot make
    two provider calls.
    """

    if job.status not in {SCORE_JOB_PENDING, SCORE_JOB_STALE}:
        return False
    if str(job.celery_task_id or ""):
        return False
    from ..tasks.scoring_tasks import score_application_job

    async_result = score_application_job.delay(
        int(job.application_id),
        job_id=int(job.id),
        force_full_score=bool(job.force_full_score),
    )
    job.celery_task_id = str(async_result.id)
    db.add(job)
    db.commit()
    return True


def cancel_pending_batch_score_jobs(db: Session, *, batch_run_id: int) -> int:
    """Terminalize only undispatched provider work owned by one batch."""

    if type(batch_run_id) is not int or batch_run_id <= 0:
        return 0
    from ..domains.assessments_runtime.scoring_batch_state import (
        progress_application_ids,
    )
    from ..models.background_job_run import (
        JOB_KIND_SCORING_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(batch_run_id),
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
        )
        .one_or_none()
    )
    if run is None or not isinstance(run.counters, dict):
        return 0
    raw_targets = run.counters.get("target_application_ids")
    target_ids = progress_application_ids(run.counters)
    if (
        not isinstance(raw_targets, list)
        or target_ids is None
        or list(target_ids) != raw_targets
    ):
        return 0

    valid_targets: list[int] = []
    for offset in range(0, len(target_ids), 500):
        valid_targets.extend(
            int(row[0])
            for row in db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.id.in_(target_ids[offset : offset + 500]),
                CandidateApplication.role_id == int(run.scope_id),
                CandidateApplication.organization_id == int(run.organization_id),
            )
            .all()
        )
    updated = 0
    finished_at = datetime.now(timezone.utc)
    for offset in range(0, len(valid_targets), 500):
        updated += int(
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.batch_run_id == int(batch_run_id),
                CvScoreJob.application_id.in_(valid_targets[offset : offset + 500]),
                CvScoreJob.role_id == int(run.scope_id),
                CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_STALE)),
            )
            .update(
                {
                    CvScoreJob.status: SCORE_JOB_ERROR,
                    CvScoreJob.error_message: "cancelled_by_recruiter",
                    CvScoreJob.finished_at: finished_at,
                },
                synchronize_session=False,
            )
            or 0
        )
    db.commit()
    return updated


__all__ = [
    "cancel_pending_batch_score_jobs",
    "create_and_dispatch_score_job",
    "ensure_score_job_published",
]
