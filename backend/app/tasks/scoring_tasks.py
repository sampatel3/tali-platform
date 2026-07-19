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
from .scoring_recovery import recover_stuck_score_jobs_impl

logger = logging.getLogger(__name__)


# A blanket cutoff duplicates queued live work. A running score has already
# been claimed by a worker and normally finishes in seconds to a few minutes.
# Keep both windows deliberately conservative while still recovering a worker
DEFAULT_PENDING_STALE_MINUTES = 360
DEFAULT_RUNNING_STALE_MINUTES = 60
DEFAULT_BROKER_FAILURE_RETRY_MINUTES = 1
# Keep the hard limit below lease expiry so the reaper cannot reclaim a live
# task even if an upstream SDK call hangs indefinitely.
SCORE_TASK_SOFT_LIMIT_SECONDS = 50 * 60
SCORE_TASK_HARD_LIMIT_SECONDS = 55 * 60


@celery_app.task(
    name="app.tasks.scoring_tasks.recover_stuck_score_jobs",
    queue="scoring",
)
def recover_stuck_score_jobs(
    *,
    limit: int = 100,
    pending_stale_minutes: int = DEFAULT_PENDING_STALE_MINUTES,
    running_stale_minutes: int = DEFAULT_RUNNING_STALE_MINUTES,
    broker_failure_retry_minutes: int = DEFAULT_BROKER_FAILURE_RETRY_MINUTES,
) -> dict:
    """Recover score jobs whose dispatch/worker died without a terminal state.

    Jobs are append-only: a stale pending/running attempt is marked ``error``
    for audit and a fresh idempotent attempt is enqueued. A latest attempt that
    already records ``broker_dispatch_failed`` is retried after a short cooling
    period, which gives public applications a five-minute recovery path instead
    of waiting for the hourly agent sweep. The role budget/credit/input gates
    are re-applied by ``enqueue_score``.
    """
    return recover_stuck_score_jobs_impl(
        limit=limit,
        pending_stale_minutes=pending_stale_minutes,
        running_stale_minutes=running_stale_minutes,
        broker_failure_retry_minutes=broker_failure_retry_minutes,
    )


@celery_app.task(
    name="app.tasks.scoring_tasks.score_application_job",
    bind=True,
    max_retries=0,
    queue="scoring",
    soft_time_limit=SCORE_TASK_SOFT_LIMIT_SECONDS,
    time_limit=SCORE_TASK_HARD_LIMIT_SECONDS,
)
def score_application_job(
    self,
    application_id: int,
    *,
    job_id: int | None = None,
    force_full_score: bool = False,
    prescreen_defer_attempt: int = 0,
) -> dict:
    """Score a single application asynchronously.

    The orchestrator wires the cache + Claude call + result persistence;
    this task is just the worker shell. Provider failures remain terminal;
    only an application-authority wait republishes a bounded fresh message.

    ``job_id`` pins the task to the specific cv_score_jobs row created by
    enqueue_score, avoiding a race where _latest_job sees an older
    error/done job because the API-server transaction hasn't committed
    yet. Falls back to _latest_job for backwards compatibility (legacy
    enqueues without job_id).

    ``force_full_score`` bypasses the pre-screen gate (used when a
    recruiter manually overrides a "pre-screened out" verdict).
    """
    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import (
        CvScoreJob,
        SCORE_JOB_ERROR,
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
        SCORE_JOB_STALE,
    )
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import (
        AutonomousScoringDeferred,
        _execute_scoring,
        _latest_job,
    )
    from ..services.score_dispatch_authority import (
        ScoreDispatchRevoked,
        discard_superseded_score_result,
        score_dispatch_is_approved,
    )
    from ..services import score_prescreen_authority as prescreen_authority
    from ..services.role_execution_guard import automatic_role_action_block_reason

    def autonomous_hold(role: Role) -> tuple[str | None, str | None]:
        detail = automatic_role_action_block_reason(role, db=db)
        if detail is None:
            return None, None
        if detail == "workspace agent is paused":
            return "deferred_workspace_paused", detail
        if detail == "role agent is paused":
            return "deferred_agent_paused", detail
        if detail == "role agent is disabled":
            return "deferred_agent_off", detail
        return "deferred_role_not_runnable", detail

    db = SessionLocal()
    try:
        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if application is None:
            logger.warning(
                "score_application_job: application_id=%s not found", application_id
            )
            return {"status": "missing", "application_id": application_id}

        if job_id is not None:
            job = (
                db.query(CvScoreJob)
                .filter(
                    CvScoreJob.id == int(job_id),
                    CvScoreJob.application_id == int(application_id),
                )
                .first()
            )
        else:
            job = _latest_job(db, application_id)
        if job is None:
            logger.warning(
                "score_application_job: no CvScoreJob row for application_id=%s job_id=%s",
                application_id,
                job_id,
            )
            return {"status": "no_job", "application_id": application_id}

        if job.status not in {SCORE_JOB_PENDING, SCORE_JOB_STALE}:
            return {
                "status": "skipped",
                "application_id": application_id,
                "job_status": job.status,
            }
        if not bool(job.dispatch_approved):
            return {
                "status": "awaiting_rescore_approval",
                "application_id": application_id,
            }

        # Fence paid work under the same role lock used by control changes.
        if bool(getattr(job, "requires_active_agent", True)):
            # Workspace Pause/Resume owns the outer execution authority. Take
            # its organization lock before the Role lock so this paid-work
            # claim and a global control change have one deterministic order.
            from ..services.workspace_agent_control import (
                workspace_agent_control_snapshot,
            )

            workspace_agent_control_snapshot(
                db,
                organization_id=int(application.organization_id),
                lock=True,
            )
        scoring_role = (
            db.query(Role)
            .filter(
                Role.id == int(application.role_id),
                Role.organization_id == int(application.organization_id),
                Role.deleted_at.is_(None),
            )
            .with_for_update()
            .populate_existing()
            .one_or_none()
            if application.role_id is not None
            else None
        )
        if scoring_role is None:
            job.status = SCORE_JOB_ERROR
            job.error_message = "role_missing_or_deleted_before_scoring"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": "error", "application_id": application_id}
        if bool(getattr(job, "requires_active_agent", True)):
            reason, authority_detail = autonomous_hold(scoring_role)
            if reason is not None:
                job.status = SCORE_JOB_STALE
                job.error_message = reason
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
                return {
                    "status": reason,
                    "application_id": application_id,
                    "role_id": int(scoring_role.id),
                    "detail": authority_detail,
                }

        from .score_job_cancellation import score_job_cancel_requested

        if score_job_cancel_requested(db, job, role_id=application.role_id):
            try:
                (
                    db.query(CvScoreJob)
                    .filter(
                        CvScoreJob.id == int(job.id),
                        CvScoreJob.status.in_([SCORE_JOB_PENDING, SCORE_JOB_STALE]),
                    )
                    .update(
                        {
                            "status": SCORE_JOB_ERROR,
                            "error_message": "cancelled_by_recruiter",
                            "finished_at": datetime.now(timezone.utc),
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
            except Exception:
                db.rollback()
            return {
                "status": "cancelled",
                "application_id": application_id,
                "role_id": int(application.role_id),
            }
        if job.status == SCORE_JOB_STALE:
            latest_job_id = (
                db.query(CvScoreJob.id)
                .filter(CvScoreJob.application_id == int(application_id))
                .order_by(CvScoreJob.id.desc())
                .limit(1)
                .scalar()
            )
            if latest_job_id != int(job.id):
                return {
                    "status": "superseded",
                    "application_id": application_id,
                    "job_status": job.status,
                }
        # Serialize Stage-1 ownership on the application row before paid work.
        lease_started_at = datetime.now(timezone.utc)
        claim = prescreen_authority.claim_score_provider_ownership(
            db,
            application_id=int(application_id),
            organization_id=int(application.organization_id),
            role_id=int(scoring_role.id),
            job_id=int(job.id),
            claimed_at=lease_started_at,
        )
        if claim == "target_missing":
            job.status = SCORE_JOB_ERROR
            job.error_message = "application_scope_changed_before_scoring"
            job.finished_at = lease_started_at
            db.commit()
            return {"status": "error", "application_id": application_id}
        if claim == "deferred":
            force_full_score = bool(force_full_score or job.force_full_score)
            db.commit()  # release application/role locks before broker I/O
            return prescreen_authority.publish_deferred_score_retry(
                score_application_job,
                application_id=int(application_id),
                job_id=int(job.id),
                force_full_score=force_full_score,
                defer_attempt=prescreen_defer_attempt,
                eager=bool(celery_app.conf.task_always_eager),
            )
        if claim != "claimed":
            db.rollback()
            if not score_dispatch_is_approved(
                db, job_id=int(job.id), application_id=int(application_id)
            ):
                return {
                    "status": "awaiting_rescore_approval",
                    "application_id": application_id,
                }
            current_status = (
                db.query(CvScoreJob.status)
                .filter(CvScoreJob.id == int(job.id))
                .scalar()
            )
            return {
                "status": "skipped",
                "application_id": application_id,
                "job_status": current_status or "missing",
            }
        db.commit()
        # Re-load after the lease commit so every subsequent write belongs to
        # the scoring transaction, not the claim transaction.
        job = db.query(CvScoreJob).filter(CvScoreJob.id == int(job.id)).first()
        if job is None:  # pragma: no cover - deleted concurrently with app
            return {"status": "no_job", "application_id": application_id}

        # Persist the exact role-intent generation this attempt will score.
        # Re-publish is allowed while the provider call is in flight; the
        # post-call check below prevents that old-JD output from overwriting the
        # freshly invalidated application score.
        from ..services.role_intent_fingerprint import (
            role_intent_fingerprint,
            role_reconfiguration_is_active,
        )

        if bool(getattr(job, "requires_active_agent", True)):
            from ..services.workspace_agent_control import (
                workspace_agent_control_snapshot,
            )

            workspace_agent_control_snapshot(
                db,
                organization_id=int(application.organization_id),
                lock=True,
            )
        scoring_role = (
            db.query(Role)
            .filter(
                Role.id == int(application.role_id),
                Role.organization_id == int(application.organization_id),
            )
            .with_for_update()
            .populate_existing()
            .one_or_none()
            if application.role_id is not None
            else None
        )
        if scoring_role is None:
            job.status = SCORE_JOB_ERROR
            job.error_message = "role_missing_before_scoring"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": "error", "application_id": application_id}
        if bool(getattr(job, "requires_active_agent", True)):
            # Claiming the lease commits and releases the first Role lock. A
            # close/cancel event can land in that narrow handoff, so reload and
            # re-authorize once more at the actual provider boundary.
            reason, authority_detail = autonomous_hold(scoring_role)
            if reason is not None:
                job.status = SCORE_JOB_STALE
                job.error_message = reason
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
                return {
                    "status": reason,
                    "application_id": application_id,
                    "role_id": int(scoring_role.id),
                    "detail": authority_detail,
                }
        scoring_intent_fingerprint = role_intent_fingerprint(scoring_role, db=db)
        job.cache_key = f"role-intent:{scoring_intent_fingerprint}"
        if role_reconfiguration_is_active(scoring_role):
            # Keep this attempt as the latest durable stale marker. It will be
            # picked up by the normal activation/cohort drain, not spent while
            # the replacement task and role configuration are incomplete.
            job.status = SCORE_JOB_STALE
            job.error_message = "deferred_role_reconfiguration"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": "deferred_role_reconfiguration",
                "application_id": application_id,
            }
        db.commit()

        try:
            _execute_scoring(
                db,
                application=application,
                job=job,
                force_full_score=bool(
                    force_full_score or getattr(job, "force_full_score", False)
                ),
            )
            # SessionLocal disables autoflush. Materialize the tentative score
            # transaction before reloading the role generation; otherwise
            # ``populate_existing`` can overwrite same-transaction role state
            # and make an intent change look unchanged. The flush is not a
            # commit, so the superseded branch below still rolls back every
            # old-intent score/cache/job write atomically.
            db.flush()
            # A workspace Pause can land while the provider call is in flight.
            # Re-acquire the outer control lock before the live Role fence and
            # hold both until either the computed result is discarded or the
            # worker commits it.
            if bool(getattr(job, "requires_active_agent", True)):
                from ..services.workspace_agent_control import (
                    workspace_agent_control_snapshot,
                )

                workspace_agent_control_snapshot(
                    db,
                    organization_id=int(scoring_role.organization_id),
                    lock=True,
                )
            # The provider call may have overlapped a role re-publish. Reload
            # the live role and compare against the durable generation captured
            # above before any computed score is committed or can queue a
            # candidate decision.
            live_role = (
                db.query(Role)
                .filter(
                    Role.id == int(scoring_role.id),
                    Role.organization_id == int(scoring_role.organization_id),
                )
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if bool(getattr(job, "requires_active_agent", True)):
                authority_reason, authority_detail = autonomous_hold(live_role)
                if authority_reason is not None:
                    db.rollback()  # discard every computed score/cache write
                    terminal_job = (
                        db.query(CvScoreJob)
                        .filter(CvScoreJob.id == int(job.id))
                        .with_for_update()
                        .one_or_none()
                    )
                    if terminal_job is not None:
                        terminal_job.status = SCORE_JOB_STALE
                        terminal_job.error_message = authority_reason
                        terminal_job.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    return {
                        "status": authority_reason,
                        "application_id": application_id,
                        "role_id": int(scoring_role.id),
                        "detail": authority_detail,
                    }
            live_fingerprint = (
                role_intent_fingerprint(live_role, db=db)
                if live_role is not None
                else None
            )
            role_intent_superseded = bool(
                live_role is None
                or live_fingerprint != scoring_intent_fingerprint
                or role_reconfiguration_is_active(live_role)
                or not score_dispatch_is_approved(
                    db,
                    job_id=int(job.id),
                    application_id=int(application_id),
                )
            )
            if role_intent_superseded:
                return discard_superseded_score_result(
                    db,
                    application_id=int(application_id),
                    role_id=int(scoring_role.id),
                    job=job,
                    live_fingerprint=live_fingerprint,
                    force_full_score=bool(force_full_score),
                )
            # A durable cancellation can land during synchronous provider I/O.
            # Discard the result before it can overwrite recruiter intent.
            if score_job_cancel_requested(db, job, role_id=application.role_id):
                db.rollback()  # discard score changes
                # Running leases are now visible in the DB, so the public
                # cancel endpoint cannot rely on its pending-only bulk update
                # to write the terminal marker for an in-flight call. Finalize
                # this attempt here after discarding the computed score.
                try:
                    (
                        db.query(CvScoreJob)
                        .filter(
                            CvScoreJob.id == int(job.id),
                            CvScoreJob.status == SCORE_JOB_RUNNING,
                        )
                        .update(
                            {
                                "status": SCORE_JOB_ERROR,
                                "error_message": "cancelled_by_recruiter",
                                "finished_at": datetime.now(timezone.utc),
                            },
                            synchronize_session=False,
                        )
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                return {
                    "status": "cancelled_mid_execution",
                    "application_id": application_id,
                    "role_id": int(application.role_id),
                }
            cache_hit = str(job.cache_hit or "")
            db.commit()
            # "Pre-screen reject goes first, before CV-match scoring." When the
            # pre-screen gate filtered this candidate out (below threshold or
            # fraud), the orchestrator short-circuited BEFORE the expensive v3
            # call but did NOT itself queue the reject. Fire it now — the
            # below-threshold verdict is persisted, so run_application_auto_reject
            # (which honours role.auto_reject_pre_screen: direct Workable disqualify vs a
            # Decision Hub card, and is idempotent) finally has a score to act on.
            # Without this the reject only ever landed via the agent cohort tick,
            # which is skipped on budget-paused roles — stranding the backlog
            # 'open'. Dispatched post-commit so the worker reads the saved verdict.
            if cache_hit in {"pre_screen_filtered", "fraud_filtered"}:
                try:
                    from .automation_tasks import run_application_auto_reject

                    run_application_auto_reject.delay(application_id)
                except Exception:  # pragma: no cover — defensive, never block scoring
                    logger.exception(
                        "post-pre-screen auto-reject dispatch failed application_id=%s",
                        application_id,
                    )
            elif application.cv_match_score is not None:
                # A real (re)score landed. Materialise its deterministic verdict
                # immediately. The shared autonomy dispatcher auto-executes
                # reversible positives for a running auto-promote role and
                # leaves safety-rail cases pending; paused/off roles still get a
                # visible recommendation without being resumed:
                #   1. existing pending card → auto-correct the SAFE subset in
                #      place (reject<->send, no hard gate, never advance); gated/
                #      advance ones keep their re-evaluate banner.
                #   2. no card at all → queue the fresh verdict now.
                # Best-effort — never blocks scoring.
                try:
                    from ..services.bulk_decision_service import (
                        auto_correct_stale_verdict,
                        ensure_deterministic_decision,
                    )

                    role = getattr(application, "role", None)
                    if role is not None:
                        corrected = auto_correct_stale_verdict(
                            db, app=application, role=role
                        )
                        queued = (
                            ensure_deterministic_decision(
                                db, app=application, role=role
                            )
                            if corrected is None
                            else None
                        )
                        if corrected or queued:
                            db.commit()
                except Exception:  # pragma: no cover — never block scoring
                    logger.exception(
                        "post-score decision ensure failed application_id=%s",
                        application_id,
                    )
                    db.rollback()

                # Slow cross-source corroboration (graph + GitHub fetch) runs
                # async + shortlist-gated — never on every score. Dispatch only
                # for a plausible match that already carries a flag worth
                # resolving (should_enrich re-checks on the worker); the fetch is
                # spent to confirm/deny a flag, not to screen everyone.
                # Best-effort — never blocks scoring.
                try:
                    from ..services.corroboration_enrichment import should_enrich

                    if should_enrich(application):
                        from .corroboration_tasks import enrich_corroboration_job

                        enrich_corroboration_job.delay(application_id)
                except Exception:  # pragma: no cover — defensive
                    logger.debug(
                        "corroboration enrich dispatch failed application_id=%s",
                        application_id,
                        exc_info=True,
                    )
            return {
                "status": job.status,
                "application_id": application_id,
                "cache_hit": cache_hit,
            }
        except (AutonomousScoringDeferred, ScoreDispatchRevoked) as exc:
            # Discard tentative writes after any between-phase revocation.
            db.rollback()
            terminal_job = (
                db.query(CvScoreJob)
                .filter(CvScoreJob.id == int(job.id))
                .with_for_update()
                .one_or_none()
            )
            batch_cancelled = exc.detail == ScoreDispatchRevoked.BATCH_CANCELLED_DETAIL
            if batch_cancelled:
                deferred_status = "cancelled"
            elif exc.detail == "rescreen approval is required":
                deferred_status = "awaiting_rescore_approval"
            elif exc.detail == "role intent changed":
                deferred_status = "superseded_role_intent"
            elif exc.detail == "workspace agent is paused":
                deferred_status = "deferred_workspace_paused"
            elif exc.detail == "role agent is paused":
                deferred_status = "deferred_agent_paused"
            elif exc.detail == "role agent is disabled":
                deferred_status = "deferred_agent_off"
            else:
                deferred_status = "deferred_role_not_runnable"
            if terminal_job is not None and terminal_job.status == SCORE_JOB_RUNNING:
                terminal_job.status = (
                    SCORE_JOB_ERROR if batch_cancelled else SCORE_JOB_STALE
                )
                terminal_job.error_message = (
                    "cancelled_by_recruiter" if batch_cancelled else deferred_status
                )
                terminal_job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": deferred_status,
                "application_id": application_id,
                "role_id": int(scoring_role.id),
                "detail": exc.detail,
                "phase": exc.phase,
            }
        except Exception:
            logger.exception(
                "score_application_job failed for application_id=%s", application_id
            )
            db.rollback()
            try:
                refreshed_job = (
                    db.query(CvScoreJob).filter(CvScoreJob.id == job.id).first()
                )
                if (
                    refreshed_job is not None
                    and refreshed_job.status == SCORE_JOB_RUNNING
                ):
                    refreshed_job.status = SCORE_JOB_ERROR
                    refreshed_job.error_message = "score_application_failed"
                    refreshed_job.finished_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                db.rollback()
            return {
                "status": "error",
                "application_id": application_id,
                "error": "score_application_failed",
            }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.scoring_tasks.batch_score_role",
    queue="scoring",
)
def batch_score_role(
    role_id: int,
    *,
    include_scored: bool = False,
    applied_after: str | None = None,
    run_id: int | None = None,
) -> dict:
    """Fetch missing CVs, then durably fan out one score job per target.

    ``run_id`` is optional for legacy callers. When supplied it must identify
    this role's scoring-batch receipt; that receipt fences duplicate worker
    deliveries and records every target that could not produce a score job.
    ``selected_total`` is the receipt's canonical cohort denominator, while
    ``not_enqueued`` reconciles targets with no score-job terminal row.
    Status readers may consume that reconciliation count only after the worker
    publishes ``fanout_complete=true``.
    """
    from .scoring_batch_worker import run_scoring_batch

    return run_scoring_batch(
        role_id,
        include_scored=include_scored,
        applied_after=applied_after,
        run_id=run_id,
    )


@celery_app.task(
    name="app.tasks.scoring_tasks.sweep_stale_scores",
    queue="scoring",
)
def sweep_stale_scores(
    *,
    limit: int = 500,
    role_id: int | None = None,
    application_ids: list[int] | None = None,
    explicit: bool = False,
    explicit_authorized_only: bool = False,
) -> dict:
    """Find applications whose scores are NULL despite having a CV, and
    enqueue them. Safety net for the hook-based invalidation in
    ``mark_role_scores_stale`` / ``mark_application_scores_stale`` —
    catches anything that slipped through (worker crash mid-batch,
    missed hook on a new mutation path, etc.).

    Two filters define "needs rescore":
    1. ``cv_text`` is present (we have something to score), AND
    2. either:
       a. a ``stale`` CvScoreJob row exists for this app (the hook
          path), OR
       b. ``pre_screen_score_100`` is NULL AND ``pre_screen_run_at``
          is NULL (never scored — but only enqueue when the
          application's role has agent mode on, otherwise scoring is
          recruiter-triggered only and we shouldn't sweep idle apps).

    Returns a dict with counts for telemetry.
    """
    from .scoring_stale_sweep import sweep_stale_scores_impl

    return sweep_stale_scores_impl(
        limit=limit,
        role_id=role_id,
        application_ids=application_ids,
        explicit=explicit,
        explicit_authorized_only=explicit_authorized_only,
    )
