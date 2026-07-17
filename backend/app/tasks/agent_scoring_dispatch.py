"""Scoring-admission helpers used by autonomous-agent Celery tasks."""

from __future__ import annotations

import logging

# Preserve the historical log channel after extracting these helpers from the
# Celery task facade.
logger = logging.getLogger("app.tasks.agent_tasks")


# How many candidates one auto-scoring dispatch admits. The same bounded helper
# is called by the hourly cohort cycle and the five-minute backlog sweep, so a
# standing backlog no longer waits an hour between 50-row chunks. New ATS
# applications still take the event-driven path immediately.
AUTO_SCORE_PER_TICK_CAP = 50

# A false→true activation is an explicit request to start the role now, so its
# one-shot first pass drains a materially larger backlog. Subsequent scheduled
# ticks return to the steady-state cap above. ``enqueue_score`` remains
# idempotent and enforces the role's monthly spend cap for every candidate.
ACTIVATION_AUTO_SCORE_CAP = 500


def _requeue_deferred_agent_scores(db, *, role, limit: int) -> tuple[int, set[int]]:
    """Replay latest score attempts that temporary authority holds deferred.

    A normal unscored drain misses forced rescores whose previous numeric score
    is still present.  Persisting the hold as a stale CvScoreJob and draining it
    first makes Resume/Turn on complete both first scores and re-scores without
    a manual click or a six-hour stale-job timeout.
    """
    from sqlalchemy import func

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import SCORE_JOB_STALE, CvScoreJob
    from ..services.cv_score_orchestrator import enqueue_score
    from ..services.role_execution_guard import (
        automatic_role_action_block_reason,
    )

    bounded = max(0, int(limit))
    if bounded <= 0:
        return 0, set()
    if automatic_role_action_block_reason(role, db=db) is not None:
        return 0, set()

    latest_id = (
        db.query(
            CvScoreJob.application_id.label("application_id"),
            func.max(CvScoreJob.id).label("job_id"),
        )
        .filter(CvScoreJob.role_id == int(role.id))
        .group_by(CvScoreJob.application_id)
        .subquery()
    )
    rows = (
        db.query(
            CvScoreJob.application_id,
            CvScoreJob.force_full_score,
        )
        .join(latest_id, CvScoreJob.id == latest_id.c.job_id)
        .filter(
            CvScoreJob.status == SCORE_JOB_STALE,
            CvScoreJob.dispatch_approved.is_(True),
            CvScoreJob.error_message.in_(
                (
                    "deferred_workspace_paused",
                    "deferred_agent_paused",
                    "deferred_agent_off",
                    "deferred_role_not_runnable",
                )
            ),
        )
        .order_by(CvScoreJob.id.asc())
        .limit(bounded)
        .all()
    )
    attempted = {int(row.application_id) for row in rows}
    touched = 0
    for application_id, force_full_score in rows:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id
                == int(role.organization_id),
                CandidateApplication.role_id == int(role.id),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if app is None:
            continue
        job = enqueue_score(
            db,
            app,
            force=True,
            bypass_pre_screen=bool(force_full_score),
            requires_active_agent=True,
        )
        if job is not None:
            touched += 1
    return touched, attempted


def _auto_enqueue_scoring(
    db,
    *,
    role,
    limit: int = AUTO_SCORE_PER_TICK_CAP,
    strict: bool = False,
) -> int:
    """Queue a scoring job for up to ``limit`` unscored candidates on the
    role. Returns the count of new/existing jobs touched.

    Skipping rules already live inside ``enqueue_score``:
    - no cv_text / no spec / no API key → returns None
    - org credit balance too low → returns None
    - role monthly $ cap reached → returns None
    - existing pending/running job → returns that job (no duplicate)

    Per-tick cap exists because the first version of this helper queued
    every unscored candidate on every tick. On a role with 1,500 unscored
    apps that meant burst-firing 1,500 Celery tasks every 60 min — far
    faster than the worker pool could chew through them, and so wasteful
    of Anthropic credits that the user's top-up ran out the same hour.
    Cap is the *steady-state* throughput; for the burst-clear-the-backlog
    case on agent activation the activation hook can pass a higher
    ``limit`` for one tick.

    We also filter out apps with a recent pre-screen error and no new
    CV upload — they'd just error again immediately. The backoff lives
    in ``application_needs_pre_screen``; we mirror it here at the SQL
    level so we don't even enqueue.
    """
    role_id = int(role.id)
    try:
        from datetime import datetime, timezone

        from sqlalchemy import and_, func, not_, or_

        from ..platform.config import settings
        from ..models.candidate_application import CandidateApplication
        from ..models.cv_score_job import (
            SCORE_JOB_PENDING,
            SCORE_JOB_RUNNING,
            SCORE_JOB_STALE,
            CvScoreJob,
        )
        from ..services.cv_score_orchestrator import enqueue_score
        from ..services.pre_screen_retry_policy import (
            pre_screen_error_retry_due_clause,
        )
        from ..services.role_execution_guard import (
            automatic_role_action_block_reason,
        )

        # This helper is also exercised directly by recovery and activation
        # code. Keep the authority at the paid-work boundary instead of relying
        # only on the outer cohort task having checked an earlier Role snapshot.
        if automatic_role_action_block_reason(role, db=db) is not None:
            return 0

        deferred_touched, deferred_app_ids = _requeue_deferred_agent_scores(
            db,
            role=role,
            limit=int(limit),
        )
        remaining_limit = max(int(limit) - deferred_touched, 0)
        if remaining_limit <= 0:
            return deferred_touched

        retry_due = pre_screen_error_retry_due_clause(CandidateApplication)
        # Re-screen is only worthwhile when the candidate uploaded a newer
        # CV after the last pre-screen run.
        fresh_cv = and_(
            CandidateApplication.cv_uploaded_at.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
            CandidateApplication.cv_uploaded_at
            > CandidateApplication.pre_screen_run_at,
        )
        active_score_job = (
            db.query(CvScoreJob.id)
            .filter(
                CvScoreJob.application_id == CandidateApplication.id,
                CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
            )
            .exists()
        )
        latest_score_job_id = (
            db.query(func.max(CvScoreJob.id))
            .filter(CvScoreJob.application_id == CandidateApplication.id)
            .correlate(CandidateApplication)
            .scalar_subquery()
        )
        unapproved_stale_job = (
            db.query(CvScoreJob.id)
            .filter(
                CvScoreJob.id == latest_score_job_id,
                CvScoreJob.status == SCORE_JOB_STALE,
                CvScoreJob.dispatch_approved.is_(False),
            )
            .exists()
        )
        unscored = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.role_id == role.id,
                CandidateApplication.cv_match_score.is_(None),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.deleted_at.is_(None),
                *(
                    [CandidateApplication.id.notin_(deferred_app_ids)]
                    if deferred_app_ids
                    else []
                ),
                # HARD GUARD: never auto-score a `sourced` prospect. It has no CV
                # (the cv_text filter below already excludes it), but keep the
                # stage gate explicit so a sourced lead is never scored before it
                # engages and transitions to `applied`.
                CandidateApplication.pipeline_stage != "sourced",
                CandidateApplication.cv_text.isnot(None),
                CandidateApplication.cv_text != "",
                # Active jobs are already admitted commitments. Excluding them
                # prevents repeated ticks from spending the whole projected
                # capacity on idempotent re-touches of the same rows.
                not_(active_score_job),
                not_(unapproved_stale_job),
                # Skip recently-errored apps unless a fresh CV beats the
                # backoff or there's no pre-screen attempt yet.
                or_(
                    CandidateApplication.pre_screen_error_reason.is_(None),
                    CandidateApplication.pre_screen_run_at.is_(None),
                    retry_due,
                    fresh_cv,
                ),
                # Skip candidates already pre-screened OUT (below threshold,
                # no error). The orchestrator NULLs cv_match_score on a
                # below-threshold complete, so without this they match the
                # cv_match_score IS NULL filter and earn a fresh CvScoreJob
                # every tick — re-running pre-screen to the same below-
                # threshold verdict (churn). Re-screen only when a newer CV
                # was uploaded.
                or_(
                    CandidateApplication.pre_screen_score_100.is_(None),
                    CandidateApplication.pre_screen_score_100
                    >= settings.PRE_SCREEN_THRESHOLD,
                    CandidateApplication.pre_screen_error_reason.isnot(None),
                    fresh_cv,
                ),
            )
            # Oldest first so the backlog drains in a fair order. The
            # next tick picks up where this one left off.
            .order_by(CandidateApplication.id.asc())
            .limit(remaining_limit)
            .all()
        )
        if unscored:
            from ..agent_runtime import budget_guard
            from ..services.pricing_service import Feature
            from ..services.usage_metering_service import (
                InsufficientCreditsError,
                reserve as reserve_usage,
            )

            try:
                score_reservation = reserve_usage(
                    db,
                    organization_id=int(role.organization_id),
                    feature=Feature.SCORE,
                )
            except InsufficientCreditsError as exc:
                # Credit depletion is a legitimate HITL boundary. Pause once
                # and say exactly what is needed; repeatedly returning zero
                # while candidates remain unscored would look healthy but leave
                # the funnel stranded forever.
                budget_guard.pause_role(
                    db,
                    role=role,
                    reason=(
                        "usage credits exhausted: "
                        f"need {exc.required}, have {exc.available}; top up to resume"
                    ),
                )
                role.agent_bootstrap_status = "failed"
                role.agent_bootstrap_error = "usage_credits_exhausted"
                role.agent_bootstrap_completed_at = datetime.now(timezone.utc)
                db.commit()
                return 0
            remaining_role = budget_guard.remaining_role_admission_microcredits(
                db,
                role=role,
                per_active_score_job=score_reservation,
            )
            if remaining_role is not None:
                role_capacity = remaining_role // max(int(score_reservation), 1)
                if len(unscored) > role_capacity:
                    logger.info(
                        "agent_cohort_tick role-budget-capped scoring burst "
                        "role_id=%s requested=%s admitted=%s remaining=%s "
                        "reservation=%s",
                        role_id,
                        len(unscored),
                        role_capacity,
                        remaining_role,
                        score_reservation,
                    )
                    unscored = unscored[:role_capacity]
            if bool(settings.USAGE_METER_LIVE):
                from ..models.organization import Organization
                from ..models.role import Role as RoleModel

                # ``enqueue_score`` performs the same soft check per job, but
                # dispatching 500 jobs in one transaction does not debit the
                # ledger: every enqueue can otherwise observe the same balance
                # and all pass. Bound this burst by the number of conservative
                # SCORE reservations the *current* balance can fund. Actual
                # scoring debits remain atomic in the workers; this is the
                # admission cap that prevents activation from knowingly
                # overcommitting an entire cohort at once.
                org = (
                    db.query(Organization)
                    .filter(Organization.id == int(role.organization_id))
                    .populate_existing()
                    .one_or_none()
                )
                available = int(getattr(org, "credits_balance", 0) or 0)
                active_org_jobs = int(
                    db.query(func.count(CvScoreJob.id))
                    .join(RoleModel, CvScoreJob.role_id == RoleModel.id)
                    .filter(
                        RoleModel.organization_id == int(role.organization_id),
                        CvScoreJob.status.in_(
                            (SCORE_JOB_PENDING, SCORE_JOB_RUNNING)
                        ),
                    )
                    .scalar()
                    or 0
                )
                committed = active_org_jobs * int(score_reservation)
                credit_capacity = max(available - committed, 0) // max(
                    int(score_reservation), 1
                )
                if len(unscored) > credit_capacity:
                    logger.info(
                        "agent_cohort_tick credit-capped scoring burst "
                        "role_id=%s requested=%s admitted=%s available=%s "
                        "reservation=%s",
                        role_id,
                        len(unscored),
                        credit_capacity,
                        available,
                        score_reservation,
                    )
                    unscored = unscored[:credit_capacity]
        touched = deferred_touched
        first_error: Exception | None = None
        for app in unscored:
            app_id = int(app.id)
            try:
                job = enqueue_score(
                    db,
                    app,
                    force=False,
                    requires_active_agent=True,
                )
                if job is not None:
                    touched += 1
            except Exception as exc:
                # Broker failures are compensated/committed by enqueue_score,
                # so the session normally remains usable. Roll back only a
                # genuinely failed SQLAlchemy transaction; unconditional
                # rollback expires the entire cohort and caller-owned setup.
                if not db.is_active:
                    db.rollback()
                first_error = first_error or exc
                logger.exception(
                    "auto-enqueue_score failed for application_id=%s role_id=%s",
                    app_id,
                    role_id,
                )
        db.commit()
        if strict and first_error is not None:
            raise RuntimeError(
                "one or more activation score jobs could not be dispatched"
            ) from first_error
        if touched:
            logger.info(
                "agent_cohort_tick auto-enqueued %d scoring job(s) for role_id=%s",
                touched,
                role_id,
            )
        return touched
    except Exception:
        logger.exception("auto-enqueue scoring failed for role_id=%s", role_id)
        db.rollback()
        if strict:
            raise
        return 0


__all__ = [
    "ACTIVATION_AUTO_SCORE_CAP",
    "AUTO_SCORE_PER_TICK_CAP",
    "_auto_enqueue_scoring",
    "_requeue_deferred_agent_scores",
]
