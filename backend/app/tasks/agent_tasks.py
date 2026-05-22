"""Celery tasks for the autonomous recruiting agent.

The actual cycle work lives in ``app.agent_runtime.orchestrator``;
these are thin wrappers that own the DB session lifecycle and Celery
plumbing.

Triggers:
- ``agent_react_to_event``: enqueued from ``app.services.application_events``
  when a relevant event happens on a role with ``agentic_mode_enabled=true``.
- ``agent_manual_run``: invoked from the manual-trigger API endpoint and
  the ``scripts/run_agent_cycle.py`` CLI.
- ``agent_daily_review_sweep`` + ``agent_daily_review_role``: beat-scheduled
  daily fan-out so the agent proactively triages each enabled role once
  a day instead of only reacting to events. The sweep iterates eligible
  roles and enqueues a per-role cron cycle for each.
"""

from __future__ import annotations

import logging
from typing import Optional

from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.agent_tasks.agent_react_to_event",
    bind=True,
    max_retries=0,
)
def agent_react_to_event(
    self,
    role_id: int,
    application_id: Optional[int] = None,
    trigger_event_id: Optional[int] = None,
) -> dict:
    """Run one autonomous cycle for ``role_id`` triggered by an event.

    Skips silently if the role has agentic mode disabled or has been
    paused — re-enabling the role is the explicit unblock.
    """
    from ..agent_runtime.event_debounce import clear_event_window
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        # Release the debounce slot before running. Events arriving during
        # this cycle then claim a fresh window and schedule the next one,
        # rather than being silently swallowed.
        clear_event_window(db, role=role)
        if not bool(role.agentic_mode_enabled):
            return {"status": "skipped", "reason": "agentic_mode_disabled", "role_id": role_id}
        if role.agent_paused_at is not None:
            return {
                "status": "skipped",
                "reason": "agent_paused",
                "role_id": role_id,
                "paused_reason": role.agent_paused_reason,
            }
        try:
            run = run_cycle(
                db,
                role=role,
                trigger="event",
                application_id=application_id,
                trigger_event_id=trigger_event_id,
            )
            db.commit()
            return {
                "status": "ok",
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "run_status": str(run.status),
                "decisions_emitted": int(run.decisions_emitted),
            }
        except Exception:
            db.rollback()
            logger.exception("agent_react_to_event failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.agent_tasks.agent_daily_review_sweep",
    bind=True,
    max_retries=0,
)
def agent_daily_review_sweep(self) -> dict:
    """Beat-scheduled fan-out. Once a day, enqueue a daily-review cron
    cycle for every role with agentic mode on and not paused.

    Stays a quick read-only sweep — the actual cycle work happens on
    ``agent_daily_review_role`` per role, so we don't hold a long
    transaction or block other beat tasks.
    """
    from ..models.role import Role
    from ..platform.database import SessionLocal

    enqueued: list[int] = []
    skipped_paused = 0
    db = SessionLocal()
    try:
        roles = (
            db.query(Role.id)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
            )
            .all()
        )
        for (role_id,) in roles:
            # Defensive: re-load + check paused inside the per-role task
            # rather than racing on stale state from this read.
            agent_daily_review_role.delay(int(role_id))
            enqueued.append(int(role_id))
    except Exception:
        logger.exception("agent_daily_review_sweep failed")
        return {"status": "error", "enqueued": enqueued}
    finally:
        db.close()
    logger.info(
        "agent_daily_review_sweep enqueued %d role cycle(s) (skipped %d paused)",
        len(enqueued),
        skipped_paused,
    )
    return {"status": "ok", "enqueued_count": len(enqueued), "role_ids": enqueued}


@celery_app.task(
    name="app.tasks.agent_tasks.agent_daily_review_role",
    bind=True,
    max_retries=0,
)
def agent_daily_review_role(self, role_id: int) -> dict:
    """Run one daily-review cycle for ``role_id``.

    Same shape as ``agent_react_to_event`` but with trigger="cron" and
    no application_id — the agent decides what's worth surfacing
    rather than focusing on a single event-driven candidate. The
    orchestrator's _initial_user_message has a cron-specific variant
    that asks the agent to triage proactively (idle candidates, fresh
    scores, stale assessments).

    Skips silently when the role isn't agent-enabled or is paused —
    keeps the sweep idempotent against stale state.
    """
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        if not bool(role.agentic_mode_enabled):
            return {"status": "skipped", "reason": "agentic_mode_disabled", "role_id": role_id}
        if role.agent_paused_at is not None:
            return {
                "status": "skipped",
                "reason": "agent_paused",
                "role_id": role_id,
                "paused_reason": role.agent_paused_reason,
            }
        try:
            run = run_cycle(
                db,
                role=role,
                trigger="cron",
                application_id=None,
            )
            db.commit()
            return {
                "status": "ok",
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "run_status": str(run.status),
                "decisions_emitted": int(run.decisions_emitted),
            }
        except Exception:
            db.rollback()
            logger.exception("agent_daily_review_role failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.agent_tasks.agent_cohort_tick_sweep",
    bind=True,
    max_retries=0,
)
def agent_cohort_tick_sweep(self) -> dict:
    """Phase 7 cohort planner: every 30 min, fan a tick to each
    agent-enabled, non-paused role.

    Replaces the per-application event trigger. The orchestrator
    surveys cohort state itself via ``survey_role_state`` and decides
    what's worth doing this cycle. With agents off, this is a no-op
    sweep — paused / disabled roles fall through.
    """
    from ..models.role import Role
    from ..platform.database import SessionLocal

    enqueued: list[int] = []
    db = SessionLocal()
    try:
        roles = (
            db.query(Role.id)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
                Role.agent_paused_at.is_(None),
            )
            .all()
        )
        for (role_id,) in roles:
            agent_cohort_tick_role.delay(int(role_id))
            enqueued.append(int(role_id))
    except Exception:
        logger.exception("agent_cohort_tick_sweep failed")
        return {"status": "error", "enqueued": enqueued}
    finally:
        db.close()
    logger.info(
        "agent_cohort_tick_sweep enqueued %d role tick(s)",
        len(enqueued),
    )
    return {"status": "ok", "enqueued_count": len(enqueued), "role_ids": enqueued}


@celery_app.task(
    name="app.tasks.agent_tasks.agent_cohort_tick_role",
    bind=True,
    max_retries=0,
)
def agent_cohort_tick_role(self, role_id: int) -> dict:
    """One cohort tick for ``role_id``.

    Two phases:
    1. Auto-enqueue scoring (pre-screen + CV match) for any unscored
       candidates on this role. The cohort planner observes the
       ``needs_pre_screen`` / ``needs_score`` counts but has no tool
       to act on them — scoring lives in a separate worker. Without
       this step the recruiter had to manually click "Process N
       candidates" before the agent could see anything new.
    2. Run the agent orchestrator with trigger="cron" — survey state
       and act on whatever is currently scored.

    Scoring is async so this tick's run_cycle won't see *this* tick's
    newly enqueued scores. The next tick (30 min later) will pick them up.
    """
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        if not bool(role.agentic_mode_enabled) or role.agent_paused_at is not None:
            return {"status": "skipped", "reason": "not_eligible", "role_id": role_id}

        # B4: concurrent cohort-tick guard. If a previous cycle for this
        # role is still running (deploy restart, clock drift, slow LLM
        # round, etc.), don't start a second one — every extra cycle is
        # wasted Sonnet spend. The C1 advisory lock inside run_cycle
        # would catch this too, but bailing here also skips the Phase-1
        # auto-enqueue scoring (which would otherwise duplicate the
        # in-flight tick's enqueues). The 10-min watchdog at
        # agent_expire_stuck_runs catches genuinely-stuck runs.
        from ..models.agent_run import AgentRun
        from datetime import datetime, timedelta, timezone as _tz
        in_flight = (
            db.query(AgentRun.id)
            .filter(
                AgentRun.role_id == role_id,
                AgentRun.status == "running",
                AgentRun.started_at > datetime.now(_tz.utc) - timedelta(minutes=15),
            )
            .first()
        )
        if in_flight is not None:
            return {
                "status": "skipped",
                "reason": "already_running",
                "role_id": role_id,
                "in_flight_run_id": int(in_flight[0]),
            }

        # Phase 1: auto-enqueue scoring for unscored candidates.
        # enqueue_score is idempotent (returns the existing pending/running
        # job when one exists) and bounded by the role's monthly $ cap via
        # the role_budget_gate it checks internally. Bounded to
        # ``AUTO_SCORE_PER_TICK_CAP`` candidates per tick so a large
        # backlog drains over the day instead of burst-firing thousands
        # of API calls into a worker pool that can't keep up.
        auto_scored = _auto_enqueue_scoring(db, role=role)

        # Phase 2: early-exit if there's nothing for the agent to do.
        # Calling run_cycle when the survey shows zero actionable work
        # burns ~$0.05 of Sonnet 4.5 per role per tick (4 roles × 48
        # ticks = ~$10/day wasted on no-op cycles). Skip when:
        # - no candidates in any decision-eligible state
        # - no open recruiter questions to react to
        # - no role-config gaps to surface
        # Auto-scoring still ran above, so newly-enqueued work isn't
        # blocked — the next tick (after scoring completes) will pick
        # them up.
        if _cycle_would_be_noop(db, role=role):
            return {
                "status": "skipped",
                "reason": "no_actionable_work",
                "role_id": role_id,
                "auto_scored_enqueued": auto_scored,
            }

        try:
            run = run_cycle(
                db,
                role=role,
                trigger="cron",
                application_id=None,
            )
            db.commit()
            return {
                "status": "ok",
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "run_status": str(run.status),
                "decisions_emitted": int(run.decisions_emitted),
                "auto_scored_enqueued": auto_scored,
            }
        except Exception:
            db.rollback()
            logger.exception("agent_cohort_tick_role failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id, "auto_scored_enqueued": auto_scored}
    finally:
        db.close()


# How many candidates the auto-scoring helper queues per cohort tick.
# Cohort ticks fire every 30 min. 50 candidates × 48 ticks = 2,400/day
# per role — enough to keep up with steady-state Workable sync without
# burst-firing thousands of API calls on a freshly-enabled role with a
# long backlog. The first tick after agent activation gets a higher cap
# via ``ACTIVATION_AUTO_SCORE_CAP`` (one-shot drain).
AUTO_SCORE_PER_TICK_CAP = 50


def _auto_enqueue_scoring(db, *, role, limit: int = AUTO_SCORE_PER_TICK_CAP) -> int:
    """Queue a scoring job for up to ``limit`` unscored candidates on the
    role. Returns the count of new/existing jobs touched.

    Skipping rules already live inside ``enqueue_score``:
    - no cv_text / no spec / no API key → returns None
    - org credit balance too low → returns None
    - role monthly $ cap reached → returns None
    - existing pending/running job → returns that job (no duplicate)

    Per-tick cap exists because the first version of this helper queued
    every unscored candidate on every tick. On a role with 1,500 unscored
    apps that meant burst-firing 1,500 Celery tasks every 30 min — far
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
    try:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import and_, or_

        from ..models.candidate_application import CandidateApplication
        from ..services.cv_score_orchestrator import enqueue_score
        from ..services.pre_screening_service import PRE_SCREEN_ERROR_BACKOFF

        backoff_cutoff = datetime.now(timezone.utc) - PRE_SCREEN_ERROR_BACKOFF
        unscored = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.role_id == role.id,
                CandidateApplication.cv_match_score.is_(None),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.cv_text.isnot(None),
                CandidateApplication.cv_text != "",
                # Skip recently-errored apps unless a fresh CV beats the
                # backoff or there's no pre-screen attempt yet.
                or_(
                    CandidateApplication.pre_screen_error_reason.is_(None),
                    CandidateApplication.pre_screen_run_at.is_(None),
                    CandidateApplication.pre_screen_run_at < backoff_cutoff,
                    and_(
                        CandidateApplication.cv_uploaded_at.isnot(None),
                        CandidateApplication.cv_uploaded_at > CandidateApplication.pre_screen_run_at,
                    ),
                ),
            )
            # Oldest first so the backlog drains in a fair order. The
            # next tick picks up where this one left off.
            .order_by(CandidateApplication.id.asc())
            .limit(int(limit))
            .all()
        )
        touched = 0
        for app in unscored:
            try:
                job = enqueue_score(db, app, force=False)
                if job is not None:
                    touched += 1
            except Exception:
                logger.exception(
                    "auto-enqueue_score failed for application_id=%s role_id=%s",
                    app.id,
                    role.id,
                )
        db.commit()
        if touched:
            logger.info(
                "agent_cohort_tick auto-enqueued %d scoring job(s) for role_id=%s",
                touched,
                role.id,
            )
        return touched
    except Exception:
        logger.exception("auto-enqueue scoring failed for role_id=%s", getattr(role, "id", None))
        db.rollback()
        return 0


def _cycle_would_be_noop(db, *, role) -> bool:
    """Return True if running ``orchestrator.run_cycle`` right now would
    produce no work — survey shows nothing actionable, no open recruiter
    questions, no role-config gaps. Used by ``agent_cohort_tick_role``
    to skip cycles that would just call Claude for the survey + a
    "nothing to do" agent_run_complete (~$0.05 of Sonnet 4.5 per cycle).

    Calls ``survey_role_state`` which is a handful of cheap COUNT
    queries — no LLM, no Anthropic round-trip. If the survey itself
    fails we fall back to running the cycle (safe default).
    """
    try:
        from ..agent_runtime.cohort_tools import survey_role_state

        survey = survey_role_state(
            db,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
        )
        if not isinstance(survey, dict) or survey.get("error"):
            return False  # survey failed → run the cycle to be safe

        # Anything actionable on the candidate side?
        counts = survey.get("counts") or {}
        actionable_states = (
            "ready_for_assessment_decision",
            "ready_for_advance_decision",
        )
        if any(int(counts.get(s) or 0) > 0 for s in actionable_states):
            return False

        # Anything to ask or answer about the role?
        if survey.get("open_questions") or survey.get("intent_gaps"):
            return False

        # Backlog work the agent should kick off? auto_enqueue_scoring
        # already handled the actual enqueue above; a non-empty
        # needs_pre_screen / needs_score count doesn't require the
        # agent itself to think (we don't want a noop summary cycle
        # while scoring is in-flight).
        return True
    except Exception:
        logger.exception("noop-check failed for role_id=%s — running cycle anyway", getattr(role, "id", None))
        return False


@celery_app.task(
    name="app.tasks.agent_tasks.agent_manual_run",
    bind=True,
    max_retries=0,
)
def agent_manual_run(self, role_id: int, application_id: Optional[int] = None) -> dict:
    """Recruiter-triggered (or CLI-triggered) one-shot run.

    Bypasses the agentic-mode-enabled check so a recruiter can dry-run
    against a role that hasn't been switched on yet, but still respects
    the paused state.
    """
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        if role.agent_paused_at is not None:
            return {
                "status": "skipped",
                "reason": "agent_paused",
                "role_id": role_id,
                "paused_reason": role.agent_paused_reason,
            }
        try:
            run = run_cycle(
                db,
                role=role,
                trigger="manual",
                application_id=application_id,
            )
            db.commit()
            return {
                "status": "ok",
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "run_status": str(run.status),
                "decisions_emitted": int(run.decisions_emitted),
            }
        except Exception:
            db.rollback()
            logger.exception("agent_manual_run failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id}
    finally:
        db.close()


# Stuck cycles. A worker crash mid-cycle (OOM, deploy restart, dyno reschedule)
# leaves the AgentRun row in status='running' forever. That hides real
# failures in /agent/status and prevents the next cohort tick from
# reasoning about "is this role currently running" correctly. Every 5 min
# the watchdog scans for runs older than STUCK_RUN_TIMEOUT and marks them
# failed.
STUCK_RUN_TIMEOUT_MINUTES = 10


@celery_app.task(
    name="app.tasks.agent_tasks.agent_expire_stuck_runs",
    bind=True,
    max_retries=0,
)
def agent_expire_stuck_runs(self) -> dict:
    """Mark agent_runs in status='running' older than the timeout as failed.

    No-op when nothing is stuck. Idempotent — re-running has no effect
    on rows already moved out of 'running'.
    """
    from datetime import datetime, timedelta, timezone

    from ..models.agent_run import AgentRun
    from ..platform.database import SessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_RUN_TIMEOUT_MINUTES)
    db = SessionLocal()
    expired_ids: list[int] = []
    try:
        stuck = (
            db.query(AgentRun)
            .filter(
                AgentRun.status == "running",
                AgentRun.started_at < cutoff,
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        for run in stuck:
            run.status = "failed"
            run.error = (
                run.error
                or f"watchdog: still running after {STUCK_RUN_TIMEOUT_MINUTES}m — worker likely crashed mid-cycle"
            )
            run.finished_at = now
            expired_ids.append(int(run.id))
        if expired_ids:
            db.commit()
            logger.warning("agent_expire_stuck_runs marked %d run(s) failed: %s", len(expired_ids), expired_ids)
    except Exception:
        db.rollback()
        logger.exception("agent_expire_stuck_runs failed")
        return {"status": "error"}
    finally:
        db.close()
    return {"status": "ok", "expired_count": len(expired_ids), "agent_run_ids": expired_ids}
