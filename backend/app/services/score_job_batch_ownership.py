"""Durable ownership checks for recruiter-triggered CV score jobs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)


def claim_live_scoring_batch(
    db: Session,
    *,
    batch_run_id: int | None,
    role_id: int,
    organization_id: int,
    application_id: int,
    owner_delivery_id: str | None = None,
) -> int | None:
    """Lock and validate the run that will own a new score-job attempt.

    The lock is held until ``enqueue_score`` commits the new ``CvScoreJob``.
    A concurrent cancellation therefore either wins before this check (and no
    job is inserted) or runs afterwards and can see/cancel the owned row.
    """

    if batch_run_id is None:
        return None
    if type(batch_run_id) is not int or batch_run_id <= 0:
        return None
    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(batch_run_id),
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role_id),
            BackgroundJobRun.organization_id == int(organization_id),
            BackgroundJobRun.status.in_(("dispatching", "queued", "running")),
            BackgroundJobRun.finished_at.is_(None),
            BackgroundJobRun.cancel_requested_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        return None
    counters = dict(run.counters or {})
    raw_targets = counters.get("target_application_ids")
    target_ids = (
        {value for value in raw_targets if type(value) is int and value > 0}
        if isinstance(raw_targets, list)
        else set()
    )
    if int(application_id) not in target_ids:
        return None
    if owner_delivery_id is not None and str(
        counters.get("fanout_owner_delivery_id") or ""
    ) != str(owner_delivery_id):
        return None
    return int(run.id)


def scoring_batch_allows_recovery(
    db: Session,
    *,
    batch_run_id: int | None,
    role_id: int,
    organization_id: int,
) -> bool:
    """Return whether an abandoned owned attempt may be redispatched."""

    if batch_run_id is None:
        return True
    if type(batch_run_id) is not int or batch_run_id <= 0:
        return False
    return (
        db.query(BackgroundJobRun.id)
        .filter(
            BackgroundJobRun.id == int(batch_run_id),
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role_id),
            BackgroundJobRun.organization_id == int(organization_id),
            BackgroundJobRun.status.in_(("dispatching", "queued", "running")),
            BackgroundJobRun.finished_at.is_(None),
            BackgroundJobRun.cancel_requested_at.is_(None),
        )
        .first()
        is not None
    )


__all__ = ["claim_live_scoring_batch", "scoring_batch_allows_recovery"]
