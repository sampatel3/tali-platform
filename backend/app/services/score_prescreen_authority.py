"""Application-scoped ownership between pre-screen and full-score workers."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Literal

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import (
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
    SCORE_JOB_STALE,
    CvScoreJob,
)
from ..models.prescreen_batch_item import (
    PRESCREEN_BATCH_ITEM_ATTEMPTING,
    PrescreenBatchItem,
)


SCORE_DEFERRED_PRESCREEN_ERROR = "deferred_prescreen_active"
SCORE_PRESCREEN_RETRY_SECONDS = 30
SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS = 20
ScoreProviderClaim = Literal["claimed", "deferred", "lost", "target_missing"]
logger = logging.getLogger("app.tasks.scoring_tasks")


def claim_score_provider_ownership(
    db: Session,
    *,
    application_id: int,
    organization_id: int,
    role_id: int,
    job_id: int,
    claimed_at: datetime,
) -> ScoreProviderClaim:
    """Serialize Stage-1 ownership with the standalone pre-screen worker.

    The pre-screen worker locks ``CandidateApplication`` before changing its
    item from ``queued`` to ``attempting``. Taking that same row lock before
    the score-job CAS gives both workflows one ordering point:

    * a committed pre-screen attempt leaves the score job pending for retry;
    * a committed running score makes the pre-screen worker skip its paid call.

    Do not lock the item row here. The pre-screen worker locks item then
    application; taking those locks in reverse order would create a deadlock.
    The caller must commit before resolving clients or entering provider code.
    """
    target = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.role_id == int(role_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if target is None:
        return "target_missing"

    active_prescreen = (
        db.query(PrescreenBatchItem.id)
        .filter(
            PrescreenBatchItem.application_id == int(application_id),
            PrescreenBatchItem.organization_id == int(organization_id),
            PrescreenBatchItem.role_id == int(role_id),
            PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
        )
        .first()
    )
    values = (
        {
            "status": SCORE_JOB_PENDING,
            "queued_at": claimed_at,
            "started_at": None,
            "finished_at": None,
            "error_message": SCORE_DEFERRED_PRESCREEN_ERROR,
        }
        if active_prescreen is not None
        else {
            "status": SCORE_JOB_RUNNING,
            "started_at": claimed_at,
            "finished_at": None,
            "error_message": None,
        }
    )
    updated = (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.id == int(job_id),
            CvScoreJob.application_id == int(application_id),
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_STALE)),
            CvScoreJob.dispatch_approved.is_(True),
        )
        .update(values, synchronize_session=False)
    )
    if updated != 1:
        return "lost"
    return "deferred" if active_prescreen is not None else "claimed"


def publish_deferred_score_retry(
    task,
    *,
    application_id: int,
    job_id: int,
    force_full_score: bool,
    defer_attempt: object,
    eager: bool,
) -> dict[str, int | str | bool]:
    """Publish after the claim transaction released every row lock."""
    try:
        attempt = max(0, min(int(defer_attempt), SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS))
    except (TypeError, ValueError):
        attempt = SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS
    scheduled = False
    if not eager and attempt < SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS:
        try:
            task.apply_async(
                args=(int(application_id),),
                kwargs={
                    "job_id": int(job_id),
                    "force_full_score": bool(force_full_score),
                    "prescreen_defer_attempt": attempt + 1,
                },
                countdown=SCORE_PRESCREEN_RETRY_SECONDS,
            )
            scheduled = True
        except Exception as exc:  # broker-loss recovery owns the pending marker
            logger.error(
                "score pre-screen defer publish failed application_id=%s error_type=%s",
                application_id,
                type(exc).__name__,
            )
    return {
        "status": SCORE_DEFERRED_PRESCREEN_ERROR,
        "application_id": int(application_id),
        "retry_scheduled": scheduled,
    }


__all__ = [
    "SCORE_DEFERRED_PRESCREEN_ERROR",
    "SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS",
    "SCORE_PRESCREEN_RETRY_SECONDS",
    "claim_score_provider_ownership",
    "publish_deferred_score_retry",
]
