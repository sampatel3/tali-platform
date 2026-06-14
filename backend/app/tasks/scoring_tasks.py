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


@celery_app.task(
    name="app.tasks.scoring_tasks.score_application_job",
    bind=True,
    max_retries=0,
    queue="scoring",
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
    from ..models.cv_score_job import CvScoreJob, SCORE_JOB_ERROR
    from ..platform.database import SessionLocal
    from ..services.cv_score_orchestrator import _execute_scoring, _latest_job

    db = SessionLocal()
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

        if job.status not in {"pending", "stale"}:
            # Another worker already picked this up — bail out.
            return {"status": "skipped", "application_id": application_id, "job_status": job.status}

        # Cooperative cancel. batch_score_role checks the same Redis flag
        # between fetch/enqueue phases, but once the per-app jobs are
        # dispatched they need to check it themselves — otherwise clicking
        # Cancel after enqueue does nothing and the recruiter waits for
        # 500+ Anthropic calls to drain naturally.
        try:
            from ..domains.assessments_runtime.applications_routes import is_batch_score_cancelled
        except Exception:  # pragma: no cover - defensive
            is_batch_score_cancelled = lambda _role_id: False  # type: ignore[assignment]
        if application.role_id is not None and is_batch_score_cancelled(int(application.role_id)):
            job.status = SCORE_JOB_ERROR
            job.error_message = "cancelled_by_recruiter"
            job.finished_at = datetime.now(timezone.utc)
            try:
                db.commit()
            except Exception:
                db.rollback()
            return {
                "status": "cancelled",
                "application_id": application_id,
                "role_id": int(application.role_id),
            }

        try:
            _execute_scoring(db, application=application, job=job, force_full_score=force_full_score)
            # Post-execution cancel guard: _execute_scoring calls Claude
            # synchronously (10-30s). If cancel fired DURING that call the
            # cancel endpoint already marked this job as error in the DB
            # (WHERE status='pending' — the job was never committed as
            # 'running' so the DB still showed 'pending'). Without this
            # check we'd commit status='done' + the score, overriding the
            # cancel and writing an unwanted result to the application.
            if application.role_id is not None and is_batch_score_cancelled(
                int(application.role_id)
            ):
                db.rollback()  # discard score changes — the cancel marker stays
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
            # (which honours role.auto_reject: direct Workable disqualify vs a
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
                # A real (re)score landed. The candidate's pending agent decision
                # may now be stale — its verdict can flip under the deterministic
                # policy (e.g. a re-score dropped a "send" below bar). Auto-correct
                # the SAFE subset in place (reject<->send only, no hard gate, never
                # advance) so a stale card doesn't strand; gated/advance ones keep
                # their banner for the recruiter. Best-effort — never blocks scoring.
                try:
                    from ..services.bulk_decision_service import (
                        auto_correct_stale_verdict,
                    )

                    role = getattr(application, "role", None)
                    if role is not None and auto_correct_stale_verdict(
                        db, app=application, role=role
                    ):
                        db.commit()
                except Exception:  # pragma: no cover — never block scoring
                    logger.exception(
                        "post-score verdict auto-correct failed application_id=%s",
                        application_id,
                    )
                    db.rollback()
            return {
                "status": job.status,
                "application_id": application_id,
                "cache_hit": cache_hit,
            }
        except Exception as exc:
            logger.exception("score_application_job failed for application_id=%s", application_id)
            db.rollback()
            try:
                refreshed_job = (
                    db.query(CvScoreJob).filter(CvScoreJob.id == job.id).first()
                )
                if refreshed_job is not None:
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

    from ..models.candidate import Candidate
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

        query = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if not include_scored:
            query = query.filter(CandidateApplication.cv_match_score.is_(None))

        if applied_after:
            from datetime import timezone as _tz
            cutoff = datetime.fromisoformat(applied_after)
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=_tz.utc)
            query = (
                query
                .join(Candidate, CandidateApplication.candidate_id == Candidate.id)
                .filter(Candidate.workable_created_at >= cutoff)
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
            is_batch_score_cancelled = lambda _: False  # type: ignore[assignment]

        fetched = 0
        fetch_failures = 0
        for app in apps:
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
            job = enqueue_score(db, app, force=include_scored)
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
def sweep_stale_scores(*, limit: int = 500) -> dict:
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
    from sqlalchemy import and_, exists

    from ..models.candidate_application import CandidateApplication
    from ..models.cv_score_job import CvScoreJob
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
        # apps every 30 min and burn token budget. The window query
        # below scopes to the most-recent job per application.
        from sqlalchemy import desc, func

        latest_job_subq = (
            db.query(
                CvScoreJob.application_id,
                func.max(CvScoreJob.queued_at).label("max_queued"),
            )
            .group_by(CvScoreJob.application_id)
            .subquery()
        )
        latest_jobs = (
            db.query(CvScoreJob)
            .join(
                latest_job_subq,
                (CvScoreJob.application_id == latest_job_subq.c.application_id)
                & (CvScoreJob.queued_at == latest_job_subq.c.max_queued),
            )
            .filter(CvScoreJob.status == "stale")
            .order_by(desc(CvScoreJob.queued_at))
            .limit(limit)
            .all()
        )
        stale_app_ids = [j.application_id for j in latest_jobs if j.application_id]

        for app_id in stale_app_ids:
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
                job = enqueue_score(db, app, force=True)
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
        }
    finally:
        db.close()
