"""Recovery implementation for abandoned CV score attempts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("app.tasks.scoring_tasks")

_STALE_RECOVERY_PENDING = "stale_attempt_recovered"
_STALE_RECOVERY_SKIPPED = "stale_attempt_recovery_skipped"


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
    already records ``broker_dispatch_failed`` or a live pre-screen defer is
    retried after a short cooling period. This closes both broker-loss windows
    without waiting for the ordinary six-hour pending cutoff. The role
    budget/credit/input gates are re-applied by ``enqueue_score``.
    """
    from datetime import timedelta

    from sqlalchemy import and_, case, exists, or_
    from sqlalchemy.orm import aliased

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import (
        CvScoreJob,
        SCORE_JOB_ERROR,
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
    )
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score
    from ..services.score_prescreen_authority import (
        SCORE_DEFERRED_PRESCREEN_ERROR,
    )

    db = SessionLocal()
    recovered = 0
    skipped = 0
    errors = 0
    retry_deferred = 0
    try:
        now = datetime.now(timezone.utc)
        pending_cutoff = now - timedelta(minutes=max(1, int(pending_stale_minutes)))
        running_cutoff = now - timedelta(minutes=max(1, int(running_stale_minutes)))
        broker_failure_cutoff = now - timedelta(
            minutes=max(1, int(broker_failure_retry_minutes))
        )
        pending_recovery_due = or_(
            CvScoreJob.queued_at < pending_cutoff,
            and_(
                CvScoreJob.error_message == SCORE_DEFERRED_PRESCREEN_ERROR,
                CvScoreJob.queued_at < broker_failure_cutoff,
            ),
        )
        newer_attempt = aliased(CvScoreJob)
        newer_attempt_exists = exists().where(
            and_(
                newer_attempt.application_id == CvScoreJob.application_id,
                or_(
                    newer_attempt.queued_at > CvScoreJob.queued_at,
                    and_(
                        newer_attempt.queued_at == CvScoreJob.queued_at,
                        newer_attempt.id > CvScoreJob.id,
                    ),
                ),
            )
        )
        recoverable_error = and_(
            CvScoreJob.status == SCORE_JOB_ERROR,
            or_(
                CvScoreJob.error_message == "broker_dispatch_failed",
                CvScoreJob.error_message.like("broker_dispatch_failed:%"),
                CvScoreJob.error_message == _STALE_RECOVERY_PENDING,
            ),
            CvScoreJob.finished_at.isnot(None),
            CvScoreJob.finished_at < broker_failure_cutoff,
            ~newer_attempt_exists,
        )
        recovery_age = case(
            (CvScoreJob.status == SCORE_JOB_ERROR, CvScoreJob.finished_at),
            else_=CvScoreJob.queued_at,
        )
        rows = (
            db.query(
                CvScoreJob.id,
                CvScoreJob.application_id,
                CvScoreJob.status,
            )
            .filter(
                CvScoreJob.dispatch_approved.is_(True),
                or_(
                    and_(
                        CvScoreJob.status == SCORE_JOB_PENDING,
                        pending_recovery_due,
                    ),
                    and_(
                        CvScoreJob.status == SCORE_JOB_RUNNING,
                        CvScoreJob.started_at.isnot(None),
                        CvScoreJob.started_at < running_cutoff,
                    ),
                    recoverable_error,
                ),
            )
            .order_by(recovery_age.asc(), CvScoreJob.id.asc())
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
        candidate_application_ids: set[int] = set()
        archived = 0
        for row_id, application_id, status in rows:
            candidate_application_ids.add(int(application_id))
            if status == SCORE_JOB_ERROR:
                continue
            claim = db.query(CvScoreJob).filter(
                CvScoreJob.id == int(row_id),
                CvScoreJob.status == status,
            )
            if status == SCORE_JOB_PENDING:
                claim = claim.filter(pending_recovery_due)
            else:
                claim = claim.filter(
                    CvScoreJob.started_at.isnot(None),
                    CvScoreJob.started_at < running_cutoff,
                )
            updated = claim.update(
                {
                    "status": SCORE_JOB_ERROR,
                    "error_message": _STALE_RECOVERY_PENDING,
                    "finished_at": now,
                },
                synchronize_session=False,
            )
            if updated == 1:
                archived += 1
        if archived:
            db.commit()

        def set_recovery_marker(
            job_id: int,
            *,
            marker: str,
            deferred_until: datetime | None = None,
        ) -> None:
            values: dict[str, object] = {CvScoreJob.error_message: marker}
            if deferred_until is not None:
                values[CvScoreJob.finished_at] = deferred_until
            db.query(CvScoreJob).filter(
                CvScoreJob.id == int(job_id),
                CvScoreJob.status == SCORE_JOB_ERROR,
                or_(
                    CvScoreJob.error_message == "broker_dispatch_failed",
                    CvScoreJob.error_message.like("broker_dispatch_failed:%"),
                    CvScoreJob.error_message == _STALE_RECOVERY_PENDING,
                ),
            ).update(
                values,
                synchronize_session=False,
            )
            db.commit()

        def recoverable_latest_attempt(
            application_id: int,
        ) -> tuple[bool, bool, int | None, int] | None:
            latest = (
                db.query(
                    CvScoreJob.id,
                    CvScoreJob.status,
                    CvScoreJob.error_message,
                    CvScoreJob.requires_active_agent,
                    CvScoreJob.force_full_score,
                    CvScoreJob.batch_run_id,
                )
                .filter(CvScoreJob.application_id == int(application_id))
                .order_by(CvScoreJob.queued_at.desc(), CvScoreJob.id.desc())
                .first()
            )
            if latest is None or latest.status != SCORE_JOB_ERROR:
                return None
            latest_error = str(latest.error_message or "")
            if not (
                latest_error == _STALE_RECOVERY_PENDING
                or latest_error == "broker_dispatch_failed"
                or latest_error.startswith("broker_dispatch_failed:")
            ):
                return None
            return (
                bool(latest.requires_active_agent),
                bool(latest.force_full_score),
                int(latest.batch_run_id) if latest.batch_run_id is not None else None,
                int(latest.id),
            )

        app_authority = {
            application_id: authority
            for application_id in sorted(candidate_application_ids)
            if (authority := recoverable_latest_attempt(application_id)) is not None
        }

        for application_id in sorted(app_authority):
            (
                requires_active_agent,
                force_full_score,
                batch_run_id,
                recovery_job_id,
            ) = app_authority[application_id]
            app = (
                db.query(CandidateApplication)
                .filter(CandidateApplication.id == application_id)
                .first()
            )
            if app is None:
                set_recovery_marker(
                    recovery_job_id,
                    marker=_STALE_RECOVERY_SKIPPED,
                )
                skipped += 1
                continue
            try:
                if batch_run_id is not None:
                    if app.role_id is None:
                        set_recovery_marker(
                            recovery_job_id,
                            marker=_STALE_RECOVERY_SKIPPED,
                        )
                        skipped += 1
                        continue
                    from ..services.score_job_batch_ownership import (
                        scoring_batch_allows_recovery,
                    )

                    if not scoring_batch_allows_recovery(
                        db,
                        batch_run_id=batch_run_id,
                        role_id=int(app.role_id),
                        organization_id=int(app.organization_id),
                    ):
                        set_recovery_marker(
                            recovery_job_id,
                            marker=_STALE_RECOVERY_SKIPPED,
                        )
                        skipped += 1
                        continue
                if (
                    enqueue_score(
                        db,
                        app,
                        force=False,
                        bypass_pre_screen=force_full_score,
                        requires_active_agent=requires_active_agent,
                        batch_run_id=batch_run_id,
                    )
                    is None
                ):
                    set_recovery_marker(
                        recovery_job_id,
                        marker=_STALE_RECOVERY_PENDING,
                        deferred_until=now,
                    )
                    skipped += 1
                    retry_deferred += 1
                else:
                    recovered += 1
            except Exception as exc:
                # ``enqueue_score`` may fail after a flush/commit boundary.
                # Reset the session so one failed redispatch cannot poison the
                # remaining recovery batch.
                db.rollback()
                set_recovery_marker(
                    recovery_job_id,
                    marker=_STALE_RECOVERY_PENDING,
                    deferred_until=now,
                )
                errors += 1
                retry_deferred += 1
                logger.error(
                    "stuck score redispatch failed application_id=%s error_type=%s",
                    application_id,
                    type(exc).__name__,
                )
        return {
            "status": "ok" if not errors else "partial",
            "stale_attempts": archived,
            "recovered": recovered,
            "skipped": skipped,
            "errors": errors,
            "retry_deferred": retry_deferred,
            "pending_stale_minutes": max(1, int(pending_stale_minutes)),
            "running_stale_minutes": max(1, int(running_stale_minutes)),
            "broker_failure_retry_minutes": max(1, int(broker_failure_retry_minutes)),
        }
    finally:
        db.close()
