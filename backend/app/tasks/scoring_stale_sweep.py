"""Durable implementation for the periodic stale-score recovery sweep."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, func, or_

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import CvScoreJob
from ..models.organization import Organization
from ..models.role import Role


logger = logging.getLogger(__name__)

_STALE_SWEEP_DEFERRED = "stale_sweep_retry_deferred"
_STALE_SWEEP_RETRY_DELAY = timedelta(minutes=5)


def _transition_stale_job(
    db,
    *,
    job_id: int,
    status: str,
    error_message: str,
    finished_at: datetime,
) -> bool:
    """Conditionally persist one quarantine/backoff without reviving newer state."""

    try:
        updated = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.id == int(job_id),
                CvScoreJob.status == "stale",
            )
            .update(
                {
                    CvScoreJob.status: status,
                    CvScoreJob.error_message: error_message,
                    CvScoreJob.finished_at: finished_at,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return updated == 1
    except Exception:
        db.rollback()
        logger.exception(
            "sweep_stale_scores: could not persist recovery state job_id=%s",
            job_id,
        )
        return False


def sweep_stale_scores_impl(
    *,
    limit: int = 500,
    role_id: int | None = None,
    application_ids: list[int] | None = None,
    explicit: bool = False,
    explicit_authorized_only: bool = False,
) -> dict:
    """Recover only the latest authorized stale attempt for each application."""

    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    enqueued = 0
    skipped = 0
    examined = 0
    try:
        now = datetime.now(timezone.utc)
        # Only the latest append-only attempt may be recovered; historical
        # stale rows must never trigger duplicate provider spend. Rank by the
        # same timestamp + primary-key ordering as the canonical status reader
        # so equal timestamps cannot make an older stale attempt look current.
        latest_job_subq = db.query(
            CvScoreJob.id.label("job_id"),
            func.row_number()
            .over(
                partition_by=CvScoreJob.application_id,
                order_by=(
                    CvScoreJob.queued_at.desc(),
                    CvScoreJob.id.desc(),
                ),
            )
            .label("latest_rank"),
        ).subquery()
        latest_jobs_query = (
            db.query(CvScoreJob)
            .join(
                latest_job_subq,
                CvScoreJob.id == latest_job_subq.c.job_id,
            )
            .join(Role, Role.id == CvScoreJob.role_id)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                latest_job_subq.c.latest_rank == 1,
                CvScoreJob.status == "stale",
                CvScoreJob.dispatch_approved.is_(True),
                Role.deleted_at.is_(None),
            )
        )
        if explicit and role_id is None:
            return {
                "status": "error",
                "reason": "explicit stale-score sweeps require role_id scope",
                "examined": 0,
                "enqueued": 0,
                "skipped": 0,
            }
        if role_id is not None:
            latest_jobs_query = latest_jobs_query.filter(
                CvScoreJob.role_id == int(role_id)
            )
        if application_ids is not None:
            latest_jobs_query = latest_jobs_query.filter(
                CvScoreJob.application_id.in_([int(value) for value in application_ids])
            )
        if explicit_authorized_only:
            # Beat recovery may spend only where a recruiter already granted
            # durable authority before the best-effort broker publish.
            latest_jobs_query = latest_jobs_query.filter(
                CvScoreJob.requires_active_agent.is_(False)
            )
        elif not explicit:
            # The global sweep recovers autonomous work only while the role and
            # workspace are live; explicit jobs carry their own authority.
            latest_jobs_query = latest_jobs_query.filter(
                or_(
                    CvScoreJob.requires_active_agent.is_(False),
                    and_(
                        Role.agentic_mode_enabled.is_(True),
                        Role.agent_paused_at.is_(None),
                        Organization.agent_workspace_paused_at.is_(None),
                    ),
                )
            )
        if not explicit:
            # A transiently inadmissible row remains visibly stale, but its
            # durable retry timestamp keeps the bounded newest window from
            # starving older eligible applications on every Beat pass.
            latest_jobs_query = latest_jobs_query.filter(
                or_(
                    CvScoreJob.error_message.is_(None),
                    CvScoreJob.error_message != _STALE_SWEEP_DEFERRED,
                    CvScoreJob.finished_at.is_(None),
                    CvScoreJob.finished_at <= now,
                )
            )
        latest_jobs = (
            latest_jobs_query.order_by(desc(CvScoreJob.queued_at)).limit(limit).all()
        )

        for stale_job in latest_jobs:
            examined += 1
            app = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.id == int(stale_job.application_id),
                    CandidateApplication.deleted_at.is_(None),
                )
                .first()
            )
            if app is None or not (app.cv_text or "").strip():
                _transition_stale_job(
                    db,
                    job_id=int(stale_job.id),
                    status="error",
                    error_message="stale_sweep_quarantined_application_unavailable",
                    finished_at=now,
                )
                skipped += 1
                continue
            batch_run_id = (
                int(stale_job.batch_run_id)
                if stale_job.batch_run_id is not None
                else None
            )
            if batch_run_id is not None:
                from ..services.score_job_batch_ownership import (
                    scoring_batch_allows_recovery,
                )

                if app.role_id is None or not scoring_batch_allows_recovery(
                    db,
                    batch_run_id=batch_run_id,
                    role_id=int(app.role_id),
                    organization_id=int(app.organization_id),
                ):
                    _transition_stale_job(
                        db,
                        job_id=int(stale_job.id),
                        status="error",
                        error_message="stale_sweep_quarantined_inactive_batch",
                        finished_at=now,
                    )
                    skipped += 1
                    continue
            try:
                job = enqueue_score(
                    db,
                    app,
                    force=False,
                    bypass_pre_screen=bool(stale_job.force_full_score),
                    requires_active_agent=(
                        False
                        if explicit or explicit_authorized_only
                        else bool(stale_job.requires_active_agent)
                    ),
                    batch_run_id=batch_run_id,
                )
                if job is not None:
                    enqueued += 1
                else:
                    _transition_stale_job(
                        db,
                        job_id=int(stale_job.id),
                        status="stale",
                        error_message=_STALE_SWEEP_DEFERRED,
                        finished_at=now + _STALE_SWEEP_RETRY_DELAY,
                    )
                    skipped += 1
            except Exception:  # pragma: no cover - defensive task boundary
                db.rollback()
                logger.exception(
                    "sweep_stale_scores: enqueue_score raised for app=%s", app.id
                )
                _transition_stale_job(
                    db,
                    job_id=int(stale_job.id),
                    status="stale",
                    error_message=_STALE_SWEEP_DEFERRED,
                    finished_at=now + _STALE_SWEEP_RETRY_DELAY,
                )
                skipped += 1

        db.commit()
        return {
            "status": "ok",
            "examined": examined,
            "enqueued": enqueued,
            "skipped": skipped,
            "role_id": int(role_id) if role_id is not None else None,
            "explicit": bool(explicit),
            "explicit_authorized_only": bool(explicit_authorized_only),
        }
    finally:
        db.close()


__all__ = ["sweep_stale_scores_impl"]
