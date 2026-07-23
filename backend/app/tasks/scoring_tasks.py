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


# A pending score can legitimately sit behind a large role batch for hours.
# Re-dispatching it after the old 15-minute blanket cutoff duplicated live
# queue work. A running score, by contrast, has already been claimed by a
# worker and normally finishes in seconds to a few minutes. Keep both windows
# deliberately conservative while still recovering a worker lost mid-call.
DEFAULT_PENDING_STALE_MINUTES = 360
DEFAULT_RUNNING_STALE_MINUTES = 60
DEFAULT_BROKER_FAILURE_RETRY_MINUTES = 1
# Keep the hard worker limit below the running-lease expiry. This ordering is
# what guarantees the reaper does not reclaim a task Celery still considers
# live, even if an upstream SDK call hangs indefinitely.
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
    from datetime import timedelta

    from sqlalchemy import and_, or_

    from ..models.candidate import Candidate
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
        pending_cutoff = now - timedelta(
            minutes=max(1, int(pending_stale_minutes))
        )
        running_cutoff = now - timedelta(
            minutes=max(1, int(running_stale_minutes))
        )
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
                        CvScoreJob.error_message.like("broker_dispatch_failed:%"),
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
                .join(
                    Candidate,
                    Candidate.id == CandidateApplication.candidate_id,
                )
                .filter(
                    CandidateApplication.id == application_id,
                    Candidate.organization_id
                    == CandidateApplication.organization_id,
                    Candidate.deleted_at.is_(None),
                )
                .first()
            )
            if app is None:
                skipped += 1
                continue
            try:
                requires_active_agent, force_full_score = app_authority[
                    application_id
                ]
                if enqueue_score(
                    db,
                    app,
                    force=False,
                    bypass_pre_screen=force_full_score,
                    requires_active_agent=requires_active_agent,
                ) is None:
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
            "broker_failure_retry_minutes": max(
                1, int(broker_failure_retry_minutes)
            ),
        }
    finally:
        db.close()


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
) -> dict:
    """Score a single application asynchronously.

    The orchestrator wires the cache + Claude call + result persistence;
    this task is just the worker shell. Retries are disabled here because
    a transient Claude failure should mark the latest job as ``error`` and
    let the recruiter trigger a manual rescore — silent retries would mask
    real issues like a malformed prompt.

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
    from ..services.role_execution_guard import (
        automatic_role_action_block_reason,
    )

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

    def newer_score_attempt(
        application_id: int,
        attempt_id: int,
    ) -> CvScoreJob | None:
        """Return a causally newer attempt, independent of wall-clock skew."""
        return (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.application_id == int(application_id),
                CvScoreJob.id > int(attempt_id),
            )
            .order_by(CvScoreJob.id.desc())
            .first()
        )

    try:
        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if application is None:
            logger.warning("score_application_job: application_id=%s not found", application_id)
            return {"status": "missing", "application_id": application_id}

        if job_id is not None:
            job = db.query(CvScoreJob).filter(CvScoreJob.id == int(job_id)).first()
        else:
            job = _latest_job(db, application_id)
        if job is None:
            logger.warning(
                "score_application_job: no CvScoreJob row for application_id=%s job_id=%s",
                application_id, job_id,
            )
            return {"status": "no_job", "application_id": application_id}

        candidate = application.candidate
        if (
            candidate is None
            or candidate.deleted_at is not None
            or int(candidate.organization_id) != int(application.organization_id)
        ):
            if job.status in {SCORE_JOB_PENDING, SCORE_JOB_STALE}:
                job.status = SCORE_JOB_ERROR
                job.error_message = "candidate_deleted_before_scoring"
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
            return {
                "status": "candidate_deleted",
                "application_id": application_id,
            }

        if job.status not in {SCORE_JOB_PENDING, SCORE_JOB_STALE}:
            # Another worker already picked this up — bail out.
            return {"status": "skipped", "application_id": application_id, "job_status": job.status}

        # Every queued score is fenced by the current role lifecycle under the
        # same lock that claims the job. A recruiter-authorized score may run
        # while an active role's agent is paused. Autonomous work additionally
        # rechecks all current run-authority conditions before the paid call.
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

        # Cooperative cancel. batch_score_role checks the same Redis flag
        # between fetch/enqueue phases, but once the per-app jobs are
        # dispatched they need to check it themselves — otherwise clicking
        # Cancel after enqueue does nothing and the recruiter waits for
        # 500+ Anthropic calls to drain naturally.
        try:
            from ..domains.assessments_runtime.applications_routes import is_batch_score_cancelled
        except Exception:  # pragma: no cover - defensive
            def is_batch_score_cancelled(_role_id):  # type: ignore[no-redef]
                return False
        if application.role_id is not None and is_batch_score_cancelled(int(application.role_id)):
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

        # Persist a durable running lease *before* the expensive scoring call.
        # Previously _execute_scoring changed this only in memory, so the DB
        # kept showing "pending" throughout a 10-30s Anthropic call. The stale
        # reaper could then mistake live work for an abandoned queue message.
        # The conditional UPDATE also makes duplicate Celery deliveries safe:
        # exactly one worker can move this attempt out of pending/stale.
        lease_started_at = datetime.now(timezone.utc)
        claimed = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.id == int(job.id),
                CvScoreJob.status.in_([SCORE_JOB_PENDING, SCORE_JOB_STALE]),
            )
            .update(
                {
                    "status": SCORE_JOB_RUNNING,
                    "started_at": lease_started_at,
                    "error_message": None,
                    "finished_at": None,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
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
        from ..components.scoring.candidate_inputs import (
            candidate_input_fingerprint_from_db,
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
        scoring_candidate_fingerprint = candidate_input_fingerprint_from_db(
            db,
            application_id=int(application.id),
            candidate_id=int(application.candidate_id),
            organization_id=int(application.organization_id),
            role_id=int(scoring_role.id),
        )
        if scoring_candidate_fingerprint is None:
            job.status = SCORE_JOB_ERROR
            job.error_message = "candidate_inputs_missing_before_scoring"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": "error", "application_id": application_id}
        newer_before_provider = newer_score_attempt(
            int(application_id), int(job.id)
        )
        if newer_before_provider is not None:
            job.status = SCORE_JOB_ERROR
            job.error_message = "superseded_before_scoring"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": "superseded_before_scoring",
                "application_id": application_id,
                "newer_job_id": int(newer_before_provider.id),
            }
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
                    force_full_score
                    or getattr(job, "force_full_score", False)
                ),
            )
            # A workspace Pause can land while the provider call is in flight.
            # Re-acquire the outer control lock before the live Role fence and
            # hold both until either the computed result is discarded or the
            # worker commits it.
            # Provider-derived application/job/event mutations must remain
            # unflushed until the platform lock order (Organization -> Role)
            # is established. RoleIntent edits already hold Role before
            # invalidating applications; flushing first would invert that
            # order and deadlock. Keep queries explicitly no-autoflush even
            # though SessionLocal currently disables autoflush globally.
            with db.no_autoflush:
                if bool(getattr(job, "requires_active_agent", True)):
                    from ..services.workspace_agent_control import (
                        workspace_agent_control_snapshot,
                    )

                    workspace_agent_control_snapshot(
                        db,
                        organization_id=int(scoring_role.organization_id),
                        lock=True,
                    )
                # The provider call may have overlapped a role re-publish.
                # Lock and reload the live generation before any computed
                # score can be persisted or drive a candidate decision.
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
                live_fingerprint = (
                    role_intent_fingerprint(live_role, db=db)
                    if live_role is not None
                    else None
                )
                live_candidate_fingerprint = candidate_input_fingerprint_from_db(
                    db,
                    application_id=int(application.id),
                    candidate_id=int(application.candidate_id),
                    organization_id=int(scoring_role.organization_id),
                    role_id=int(scoring_role.id),
                    lock=True,
                )
                newer_attempt_after_provider = newer_score_attempt(
                    int(application_id), int(job.id)
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
            role_intent_superseded = bool(
                live_role is None
                or live_fingerprint != scoring_intent_fingerprint
                or role_reconfiguration_is_active(live_role)
            )
            candidate_inputs_superseded = bool(
                live_candidate_fingerprint != scoring_candidate_fingerprint
            )
            score_attempt_superseded = newer_attempt_after_provider is not None
            if (
                role_intent_superseded
                or candidate_inputs_superseded
                or score_attempt_superseded
            ):
                superseded_reason = (
                    "superseded_role_intent"
                    if role_intent_superseded
                    else (
                        "superseded_candidate_inputs"
                        if candidate_inputs_superseded
                        else "superseded_score_attempt"
                    )
                )
                recovery_reason = (
                    "rescore_after_role_reconfiguration"
                    if role_intent_superseded
                    else (
                        "rescore_after_candidate_inputs_changed"
                        if candidate_inputs_superseded
                        else "rescore_after_newer_score_attempt"
                    )
                )
                db.rollback()  # discard every obsolete provider-derived write
                terminal_job = (
                    db.query(CvScoreJob)
                    .filter(CvScoreJob.id == int(job.id))
                    .with_for_update()
                    .one_or_none()
                )
                now = datetime.now(timezone.utc)
                if terminal_job is not None:
                    terminal_job.status = SCORE_JOB_ERROR
                    terminal_job.error_message = superseded_reason
                    terminal_job.finished_at = now
                if newer_score_attempt(int(application_id), int(job.id)) is None:
                    db.add(
                        CvScoreJob(
                            application_id=int(application_id),
                            role_id=int(scoring_role.id),
                            status=SCORE_JOB_STALE,
                            cache_key=(
                                f"role-intent:{live_fingerprint}"
                                if live_fingerprint
                                else None
                            ),
                            error_message=recovery_reason,
                            requires_active_agent=bool(
                                getattr(job, "requires_active_agent", True)
                            ),
                            force_full_score=bool(
                                getattr(job, "force_full_score", False)
                                or force_full_score
                            ),
                            queued_at=now,
                        )
                    )
                db.commit()
                return {
                    "status": superseded_reason,
                    "application_id": application_id,
                    "role_id": int(scoring_role.id),
                }
            # The generation is current. Flush all provider-derived writes
            # while the live Role lock remains held; a later recruiter edit
            # waits, then invalidates this newly committed generation.
            db.flush()
            # Post-execution cancel guard: _execute_scoring calls Claude
            # synchronously (10-30s). If cancel fired DURING that call, the
            # Redis flag is the cross-process interrupt signal. Without this
            # check we'd commit status='done' + the score despite the request.
            if application.role_id is not None and is_batch_score_cancelled(
                int(application.role_id)
            ):
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
            from ..components.scoring.freshness import ScoreGenerationToken

            completed_score_generation = ScoreGenerationToken(
                application_id=int(application_id),
                role_id=int(scoring_role.id),
                job_id=int(job.id),
                role_intent_fingerprint=str(scoring_intent_fingerprint),
            )
            completed_corroboration_generation = None
            try:
                from ..services.corroboration_enrichment import (
                    capture_corroboration_generation,
                    should_enrich,
                )

                scoring_candidate = getattr(application, "candidate", None)
                if should_enrich(application) and scoring_candidate is not None:
                    # Capture while the score/candidate locks are still held.
                    # A mutation after this commit therefore makes the worker
                    # skip before any external corroboration work is spent.
                    completed_corroboration_generation = (
                        capture_corroboration_generation(
                            application=application,
                            candidate=scoring_candidate,
                            score_generation=completed_score_generation,
                            candidate_fingerprint=scoring_candidate_fingerprint,
                        )
                    )
            except Exception:  # pragma: no cover - optional enrichment only
                logger.debug(
                    "corroboration generation capture failed application_id=%s",
                    application_id,
                    exc_info=True,
                )
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
                            db,
                            app=application,
                            role=role,
                            expected_score_generation=completed_score_generation,
                        )
                        queued = (
                            ensure_deterministic_decision(
                                db,
                                app=application,
                                role=role,
                                expected_score_generation=completed_score_generation,
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
                    if (
                        completed_corroboration_generation is not None
                        and should_enrich(application)
                    ):
                        from .corroboration_tasks import enrich_corroboration_job

                        # Keep the historical one-argument broker contract so
                        # a newly deployed producer remains compatible with an
                        # old scoring worker during a rolling deployment. The
                        # worker captures and fences the current durable
                        # generation before any external enrichment call.
                        enrich_corroboration_job.delay(application_id)
                except Exception:  # pragma: no cover — defensive
                    logger.debug(
                        "corroboration enrich dispatch failed application_id=%s",
                        application_id, exc_info=True,
                    )
            return {
                "status": job.status,
                "application_id": application_id,
                "cache_hit": cache_hit,
            }
        except AutonomousScoringDeferred as exc:
            # A workspace Pause committed between provider phases.  Discard
            # every tentative pre-screen/score/cache write from this attempt,
            # then retain a durable stale marker for Resume/cohort recovery.
            # The already-in-flight provider request cannot be cancelled, but
            # no later phase is allowed to start after the live authority
            # recheck observes the pause.
            db.rollback()
            terminal_job = (
                db.query(CvScoreJob)
                .filter(CvScoreJob.id == int(job.id))
                .with_for_update()
                .one_or_none()
            )
            if exc.detail == "workspace agent is paused":
                deferred_status = "deferred_workspace_paused"
            elif exc.detail == "role agent is paused":
                deferred_status = "deferred_agent_paused"
            elif exc.detail == "role agent is disabled":
                deferred_status = "deferred_agent_off"
            else:
                deferred_status = "deferred_role_not_runnable"
            if terminal_job is not None and terminal_job.status == SCORE_JOB_RUNNING:
                terminal_job.status = SCORE_JOB_STALE
                terminal_job.error_message = deferred_status
                terminal_job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": deferred_status,
                "application_id": application_id,
                "role_id": int(scoring_role.id),
                "detail": exc.detail,
                "phase": exc.phase,
            }
        except Exception as exc:
            logger.exception("score_application_job failed for application_id=%s", application_id)
            db.rollback()
            try:
                refreshed_job = (
                    db.query(CvScoreJob).filter(CvScoreJob.id == job.id).first()
                )
                if refreshed_job is not None and refreshed_job.status == SCORE_JOB_RUNNING:
                    refreshed_job.status = SCORE_JOB_ERROR
                    refreshed_job.error_message = f"task_exception: {exc}"
                    refreshed_job.finished_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                db.rollback()
            return {"status": "error", "application_id": application_id, "error": str(exc)}
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
) -> dict:
    """Fan out per-application scoring jobs for every application under a role.

    For Workable-imported applications missing ``cv_text``, the CV is fetched
    from Workable inline before per-app score tasks are dispatched. Without
    this, ``enqueue_score`` returns None for missing-CV apps and they're
    silently dropped from the batch — which is exactly what was happening
    in production before this fix (counted 1/600 because only 1 app had a
    CV pre-fetched).

    The fetch is sequential (~3-5s per Workable candidate). Per-app
    scoring then fans out to parallel ``score_application_job`` tasks. For
    600 candidates the fetch loop takes ~30-50 min; scoring runs in the
    background after that.

    ``applied_after`` (ISO date string, e.g. "2026-01-01") filters to
    candidates whose Workable application date is on or after that date.
    Used for backfills where we only want a specific cohort.
    """
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "missing_role", "role_id": role_id}

        org = (
            db.query(Organization)
            .filter(Organization.id == role.organization_id)
            .first()
        )

        from ..services.logical_role_batch_operations import (
            context_fetch_transport,
            context_has_cv,
            filter_contexts_applied_after,
            is_related_role,
            logical_role_contexts,
            ordinary_score_targets_query,
            parse_applied_after,
            related_score_targets,
        )

        try:
            cutoff = parse_applied_after(applied_after)
        except ValueError:
            return {
                "status": "invalid_applied_after",
                "role_id": role_id,
                "applied_after": applied_after,
            }

        if is_related_role(role):
            from ..domains.assessments_runtime.applications_routes import (
                _try_fetch_cv_from_workable,
                is_batch_score_cancelled,
            )
            from ..services.related_role_rescreen_service import (
                RelatedRoleRescreenUnavailableError,
                rescreen_related_role_candidates,
            )

            contexts = filter_contexts_applied_after(
                logical_role_contexts(db, role=role),
                cutoff=cutoff,
            )
            targets = related_score_targets(
                contexts,
                include_scored=include_scored,
            )
            fetched = 0
            fetch_failures = 0
            seen_transport_ids: set[int] = set()
            for context in targets:
                if is_batch_score_cancelled(role_id):
                    db.rollback()
                    return {
                        "status": "cancelled",
                        "role_id": role_id,
                        "count": 0,
                        "fetched": fetched,
                        "fetch_failures": fetch_failures,
                    }
                if context_has_cv(context):
                    continue
                transport = context_fetch_transport(context)
                if (
                    transport is None
                    or int(transport.id) in seen_transport_ids
                    or str(transport.source or "") != "workable"
                    or org is None
                ):
                    continue
                seen_transport_ids.add(int(transport.id))
                try:
                    if _try_fetch_cv_from_workable(
                        transport,
                        transport.candidate,
                        db,
                        org,
                    ):
                        fetched += 1
                    else:
                        fetch_failures += 1
                except Exception:
                    fetch_failures += 1
                    logger.exception(
                        "Related-role batch CV fetch failed "
                        "role_id=%s application_id=%s",
                        role_id,
                        context.application_id,
                    )
            db.commit()
            try:
                outcome = rescreen_related_role_candidates(
                    db,
                    role,
                    reason="recruiter:related_role_batch_score",
                    application_ids=[
                        context.application_id for context in targets
                    ],
                    require_all_memberships=True,
                )
            except RelatedRoleRescreenUnavailableError as exc:
                return {
                    "status": "role_state_changed",
                    "role_id": role_id,
                    "count": 0,
                    "error": str(exc),
                }
            return {
                "status": "enqueued",
                "role_id": role_id,
                "count": outcome.reset_count,
                "fetched": fetched,
                "fetch_failures": fetch_failures,
                "pre_screened_out": 0,
                "scoring_scope": "related_role_evaluation",
                "summary": outcome.as_dict(),
            }

        query = ordinary_score_targets_query(
            db,
            organization_id=int(role.organization_id),
            role_id=int(role_id),
            include_scored=include_scored,
            applied_after=cutoff,
        ).options(
            joinedload(CandidateApplication.candidate),
        )

        apps = query.all()

        # 1. Fetch missing CVs (Workable apps + candidate-level fallback).
        # Lazy import to avoid circular dependency: applications_routes
        # imports services, so we can't import it at module load.
        try:
            from ..domains.assessments_runtime.applications_routes import (
                _try_fetch_cv_from_workable,
                is_batch_score_cancelled,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("Failed to import _try_fetch_cv_from_workable: %s", exc)
            _try_fetch_cv_from_workable = None  # type: ignore[assignment]

            def is_batch_score_cancelled(_role_id):  # type: ignore[no-redef]
                return False

        fetched = 0
        fetch_failures = 0
        for app in apps:
            if (
                app.candidate is None
                or app.candidate.deleted_at is not None
                or int(app.candidate.organization_id) != int(role.organization_id)
            ):
                continue
            # Cooperative cancel between candidates so the recruiter
            # can stop a 600-candidate batch without restarting the worker.
            if is_batch_score_cancelled(role_id):
                logger.info(
                    "batch_score_role cancelled during fetch phase for role_id=%s",
                    role_id,
                )
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                return {
                    "status": "cancelled",
                    "role_id": role_id,
                    "count": 0,
                    "fetched": fetched,
                    "fetch_failures": fetch_failures,
                }
            if (app.cv_text or "").strip():
                continue
            try:
                # Candidate-level CV already extracted? Promote it.
                if app.candidate and (app.candidate.cv_text or "").strip():
                    app.cv_file_url = app.candidate.cv_file_url
                    app.cv_filename = app.candidate.cv_filename
                    app.cv_text = app.candidate.cv_text
                    app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    fetched += 1
                elif (
                    (app.source or "") == "workable"
                    and org is not None
                    and _try_fetch_cv_from_workable is not None
                ):
                    if _try_fetch_cv_from_workable(app, app.candidate, db, org):
                        fetched += 1
                    else:
                        fetch_failures += 1
            except Exception:
                logger.exception(
                    "Batch CV fetch failed for application_id=%s", app.id
                )
                fetch_failures += 1
        try:
            db.commit()
        except Exception:
            logger.exception("Failed to commit batch CV fetch results")
            db.rollback()

        # 2. Re-load apps so the freshly-set cv_text is visible. Not strictly
        # necessary since we kept the same session, but the commit may have
        # expired some attributes — explicit refresh is cheap.
        apps = query.all()

        enqueued = 0
        pre_screened_out = 0
        for app in apps:
            if (
                app.candidate is None
                or app.candidate.deleted_at is not None
                or int(app.candidate.organization_id) != int(role.organization_id)
            ):
                continue
            if is_batch_score_cancelled(role_id):
                logger.info(
                    "batch_score_role cancelled during enqueue phase for role_id=%s "
                    "(enqueued %d, remaining %d)",
                    role_id, enqueued, len(apps) - enqueued,
                )
                db.commit()
                return {
                    "status": "cancelled",
                    "role_id": role_id,
                    "count": enqueued,
                    "fetched": fetched,
                    "fetch_failures": fetch_failures,
                    "pre_screened_out": pre_screened_out,
                }
            job = enqueue_score(
                db,
                app,
                force=include_scored,
                requires_active_agent=False,
            )
            if job is not None:
                enqueued += 1
                # When inline (no Celery), the job has already run by now —
                # count gate-filtered verdicts so the toaster can show progress.
                if str(getattr(job, "cache_hit", "") or "") == "pre_screen_filtered":
                    pre_screened_out += 1
        db.commit()

        # Clear the flag after a clean run so the next batch starts fresh.
        # If the run *was* cancelled we already early-returned above; in
        # both early-return cases the cancel endpoint clears the flag too.
        try:
            from ..domains.assessments_runtime.applications_routes import (
                _BATCH_SCORE_CANCEL_PREFIX,
                _clear_cancel_flag,
            )
            _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role_id)
        except Exception:
            pass

        return {
            "status": "enqueued",
            "role_id": role_id,
            "count": enqueued,
            "fetched": fetched,
            "fetch_failures": fetch_failures,
            "pre_screened_out": pre_screened_out,
        }
    finally:
        db.close()


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
    from sqlalchemy import and_, or_

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import CvScoreJob
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    enqueued = 0
    skipped = 0
    examined = 0
    try:
        # Find apps whose LATEST CvScoreJob row is ``status='stale'``.
        # ``CvScoreJob`` rows are append-only — a successful rescore
        # adds a new ``pending`` / ``running`` / ``done`` row instead
        # of converting the old stale one — so naive
        # ``status == "stale"`` queries would re-enqueue already-fixed
        # apps on every safety-net run and burn token budget. The window query
        # below scopes to the most-recent job per application.
        from sqlalchemy import func

        latest_job_subq = (
            db.query(
                CvScoreJob.application_id,
                func.max(CvScoreJob.id).label("max_id"),
            )
            .group_by(CvScoreJob.application_id)
            .subquery()
        )
        latest_jobs_query = (
            db.query(CvScoreJob)
            .join(
                latest_job_subq,
                (CvScoreJob.application_id == latest_job_subq.c.application_id)
                & (CvScoreJob.id == latest_job_subq.c.max_id),
            )
            .join(Role, Role.id == CvScoreJob.role_id)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                CvScoreJob.status == "stale",
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
        if application_ids:
            latest_jobs_query = latest_jobs_query.filter(
                CvScoreJob.application_id.in_(
                    [int(value) for value in application_ids]
                )
            )
        if not explicit:
            # The periodic global safety net is recovery, not fresh authority.
            # Autonomous stale work is eligible only for a currently running
            # role; explicit jobs retain their own recruiter authority.
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
        latest_jobs = (
            latest_jobs_query.order_by(CvScoreJob.id.asc())
            .limit(limit)
            .all()
        )

        for stale_job in latest_jobs:
            app_id = int(stale_job.application_id)
            examined += 1
            app = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.id == app_id,
                    CandidateApplication.deleted_at.is_(None),
                )
                .first()
            )
            if app is None or not (app.cv_text or "").strip():
                skipped += 1
                continue
            try:
                job = enqueue_score(
                    db,
                    app,
                    force=True,
                    bypass_pre_screen=bool(stale_job.force_full_score),
                    requires_active_agent=(
                        False
                        if explicit
                        else bool(stale_job.requires_active_agent)
                    ),
                )
                if job is not None:
                    enqueued += 1
                else:
                    skipped += 1
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "sweep_stale_scores: enqueue_score raised for app=%s", app.id
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
        }
    finally:
        db.close()
