"""Recovery implementation for abandoned CV score attempts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("app.tasks.scoring_tasks")


def recover_stuck_score_jobs_impl(
    *,
    limit: int,
    pending_stale_minutes: int,
    running_stale_minutes: int,
    broker_failure_retry_minutes: int,
) -> dict:
    """Recover score jobs whose dispatch/worker died without a terminal state.

    Jobs are append-only: a stale pending/running attempt is marked ``error``
    for audit and a fresh idempotent attempt is enqueued. A latest attempt that
    already records ``broker_dispatch_failed`` is retried after a short cooling
    period, which gives public applications a five-minute recovery path instead
    of waiting for the hourly agent sweep. The role budget/credit/input gates
    are re-applied by ``enqueue_score``.
    """
    from datetime import timedelta

    from sqlalchemy import and_, or_

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import (
        CvScoreJob,
        SCORE_JOB_ERROR,
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
    )
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    recovered = 0
    skipped = 0
    errors = 0
    try:
        now = datetime.now(timezone.utc)
        pending_cutoff = now - timedelta(minutes=max(1, int(pending_stale_minutes)))
        running_cutoff = now - timedelta(minutes=max(1, int(running_stale_minutes)))
        broker_failure_cutoff = now - timedelta(
            minutes=max(1, int(broker_failure_retry_minutes))
        )
        rows = (
            db.query(
                CvScoreJob.id,
                CvScoreJob.application_id,
                CvScoreJob.status,
                CvScoreJob.requires_active_agent,
                CvScoreJob.force_full_score,
            )
            .filter(
                CvScoreJob.dispatch_approved.is_(True),
                or_(
                    and_(
                        CvScoreJob.status == SCORE_JOB_PENDING,
                        CvScoreJob.queued_at < pending_cutoff,
                    ),
                    and_(
                        CvScoreJob.status == SCORE_JOB_RUNNING,
                        CvScoreJob.started_at.isnot(None),
                        CvScoreJob.started_at < running_cutoff,
                    ),
                    and_(
                        CvScoreJob.status == SCORE_JOB_ERROR,
                        or_(
                            CvScoreJob.error_message == "broker_dispatch_failed",
                            CvScoreJob.error_message.like("broker_dispatch_failed:%"),
                        ),
                        CvScoreJob.finished_at.isnot(None),
                        CvScoreJob.finished_at < broker_failure_cutoff,
                    ),
                )
            )
            .order_by(CvScoreJob.queued_at.asc(), CvScoreJob.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        # The candidate query and this update are separate statements. Claim
        # each row with a status+timestamp predicate so a worker moving a
        # pending row to running between them wins; the reaper must never
        # archive newly-active work based on its stale snapshot.
        #
        # More than one abandoned attempt can exist for the same application.
        # Archive every row we successfully claim, but enqueue at most one
        # replacement score.
        app_authority: dict[int, tuple[bool, bool]] = {}
        archived = 0
        for (
            row_id,
            application_id,
            status,
            requires_active_agent,
            force_full_score,
        ) in rows:
            if status == SCORE_JOB_ERROR:
                latest_id = (
                    db.query(CvScoreJob.id)
                    .filter(CvScoreJob.application_id == int(application_id))
                    .order_by(CvScoreJob.id.desc())
                    .limit(1)
                    .scalar()
                )
                if latest_id == int(row_id):
                    app_authority[int(application_id)] = (
                        bool(requires_active_agent),
                        bool(force_full_score),
                    )
                continue
            claim = db.query(CvScoreJob).filter(
                CvScoreJob.id == int(row_id),
                CvScoreJob.status == status,
            )
            if status == SCORE_JOB_PENDING:
                claim = claim.filter(CvScoreJob.queued_at < pending_cutoff)
            else:
                claim = claim.filter(
                    CvScoreJob.started_at.isnot(None),
                    CvScoreJob.started_at < running_cutoff,
                )
            updated = claim.update(
                {
                    "status": SCORE_JOB_ERROR,
                    "error_message": "stale_attempt_recovered",
                    "finished_at": now,
                },
                synchronize_session=False,
            )
            if updated == 1:
                archived += 1
                app_authority[int(application_id)] = (
                    bool(requires_active_agent),
                    bool(force_full_score),
                )
        if archived:
            db.commit()

        for application_id in sorted(app_authority):
            app = (
                db.query(CandidateApplication)
                .filter(CandidateApplication.id == application_id)
                .first()
            )
            if app is None:
                skipped += 1
                continue
            try:
                requires_active_agent, force_full_score = app_authority[application_id]
                if (
                    enqueue_score(
                        db,
                        app,
                        force=False,
                        bypass_pre_screen=force_full_score,
                        requires_active_agent=requires_active_agent,
                    )
                    is None
                ):
                    skipped += 1
                else:
                    recovered += 1
            except Exception:
                # ``enqueue_score`` may fail after a flush/commit boundary.
                # Reset the session so one failed redispatch cannot poison the
                # remaining recovery batch.
                db.rollback()
                errors += 1
                logger.exception(
                    "stuck score redispatch failed application_id=%s",
                    application_id,
                )
        return {
            "status": "ok" if not errors else "partial",
            "stale_attempts": archived,
            "recovered": recovered,
            "skipped": skipped,
            "errors": errors,
            "pending_stale_minutes": max(1, int(pending_stale_minutes)),
            "running_stale_minutes": max(1, int(running_stale_minutes)),
            "broker_failure_retry_minutes": max(1, int(broker_failure_retry_minutes)),
        }
    finally:
        db.close()
