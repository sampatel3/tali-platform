"""Compact database reads for recruiter-visible background progress."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from ...models.cv_score_job import (
    CvScoreJob,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
)
from ...models.role import Role


def batch_score_terminal_counts(
    db: Session,
    *,
    role_id: int,
    started_at: datetime,
    application_ids: Iterable[int] | None = None,
) -> tuple[int, int, int]:
    """Return one latest-attempt terminal contribution per application."""

    scoped_application_ids = (
        tuple(sorted({int(value) for value in application_ids}))
        if application_ids is not None
        else None
    )
    if scoped_application_ids == ():
        return 0, 0, 0

    relevant_filters = [
        CvScoreJob.role_id == int(role_id),
        or_(
            CvScoreJob.finished_at >= started_at,
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
        ),
    ]
    if scoped_application_ids is not None:
        relevant_filters.append(
            CvScoreJob.application_id.in_(scoped_application_ids)
        )

    latest_jobs = (
        db.query(
            CvScoreJob.application_id.label("application_id"),
            CvScoreJob.status.label("status"),
            CvScoreJob.cache_hit.label("cache_hit"),
            func.row_number()
            .over(
                partition_by=CvScoreJob.application_id,
                order_by=CvScoreJob.id.desc(),
            )
            .label("recency_rank"),
        )
        # enqueue_score can reuse a pre-existing active attempt, so active
        # rows remain relevant even when they predate the batch boundary.
        .filter(*relevant_filters)
        .subquery()
    )

    non_filtered_done = and_(
        latest_jobs.c.status == SCORE_JOB_DONE,
        or_(
            latest_jobs.c.cache_hit.is_(None),
            latest_jobs.c.cache_hit != "pre_screen_filtered",
        ),
    )
    pre_screen_filtered = and_(
        latest_jobs.c.status == SCORE_JOB_DONE,
        latest_jobs.c.cache_hit == "pre_screen_filtered",
    )
    row = (
        db.query(
            func.coalesce(func.sum(case((non_filtered_done, 1), else_=0)), 0),
            func.coalesce(
                func.sum(
                    case((latest_jobs.c.status == SCORE_JOB_ERROR, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((pre_screen_filtered, 1), else_=0)),
                0,
            ),
        )
        .select_from(latest_jobs)
        .filter(latest_jobs.c.recency_rank == 1)
        .one()
    )
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def batch_score_role_name(
    db: Session,
    *,
    progress: dict,
    role_id: int,
    organization_id: int,
) -> str:
    """Return cached role identity, querying only for restart/legacy recovery."""

    cached = str(progress.get("role_name") or "")
    if cached:
        return cached
    value = (
        db.query(Role.name)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
        )
        .scalar()
    )
    return str(value or "")


__all__ = ["batch_score_role_name", "batch_score_terminal_counts"]
