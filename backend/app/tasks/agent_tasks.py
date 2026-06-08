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


# Per-cycle Celery time limits for the cycle-running agent tasks
# (react_to_event / daily_review_role / cohort_tick_role / manual_run).
# Sized from real prod data (3-day window): legitimate cycles top out at
# ~171s (p95 140s, round-cap aborts 129s); genuine hangs ran 400-791s
# before the 10-min watchdog caught them. A 300s soft limit is ~1.75× the
# observed legitimate max — ample headroom for Anthropic latency spikes —
# while sitting cleanly below the hang range, so a stuck cycle is broken
# out at ~5 min instead of occupying a worker slot for 10. The 360s hard
# limit force-kills the worker child if the soft handler itself wedges
# (e.g. the hang is a DB call). SoftTimeLimitExceeded surfaces as a
# normal exception inside run_cycle's Anthropic try/except, marking the
# run failed; the watchdog (now 7m) is the backstop for non-LLM hangs.
AGENT_CYCLE_SOFT_LIMIT_S = 300
AGENT_CYCLE_HARD_LIMIT_S = 360


@celery_app.task(
    name="app.tasks.agent_tasks.agent_react_to_event",
    bind=True,
    max_retries=0,
    soft_time_limit=AGENT_CYCLE_SOFT_LIMIT_S,
    time_limit=AGENT_CYCLE_HARD_LIMIT_S,
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
    soft_time_limit=AGENT_CYCLE_SOFT_LIMIT_S,
    time_limit=AGENT_CYCLE_HARD_LIMIT_S,
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


# Bounded per-run cap so a large stranded backlog drains over several ticks
# instead of bursting thousands of auto-reject tasks into the worker pool.
PRE_SCREEN_REJECT_SWEEP_CAP = 500


@celery_app.task(
    name="app.tasks.agent_tasks.pre_screen_reject_sweep",
    bind=True,
    max_retries=0,
)
def pre_screen_reject_sweep(self, cap: int = PRE_SCREEN_REJECT_SWEEP_CAP) -> dict:
    """Cull already-pre-screened, below-threshold candidates — INCLUDING on
    budget-paused roles AND agent-off roles.

    The pre-screen reject is deterministic and free (no Anthropic spend), so it
    must surface regardless of the agent toggle or a budget auto-pause — the
    agent governs autonomous execution, not whether a deterministic reject is
    queued for human review. The agent cohort tick only manages agent-on roles
    (and skips paused ones), which stranded the below-threshold backlog 'open'
    with no reject on agent-off roles. This sweep is the catch-up net for all
    roles; it honours ``role.auto_reject`` downstream (Workable disqualify only
    when eligible, else a Decision Hub card). It does NOT score
    or run the LLM; it only re-dispatches ``run_application_auto_reject`` for
    open, below-threshold, not-yet-fully-scored candidates that have no
    pending decision yet. That task is idempotent and honours
    ``role.auto_reject`` (direct Workable disqualify vs a Decision Hub card),
    so re-running is safe.

    Selection mirrors ``backfill_existing_below_threshold``: a numeric
    ``pre_screen_score_100`` under the role's cutoff, OR a 'Below threshold'
    recommendation (covers must-have misses / invalidated scores). Fully
    cv_match-scored candidates are excluded — the agent owns those.
    """
    from sqlalchemy import and_, func, or_, exists

    from ..models.agent_decision import AgentDecision
    from ..models.candidate_application import CandidateApplication
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..tasks.automation_tasks import run_application_auto_reject

    _DEFAULT_CUTOFF = 50
    effective_cutoff = func.coalesce(Role.score_threshold, _DEFAULT_CUTOFF)
    dispatched: list[int] = []
    db = SessionLocal()
    try:
        has_pending = (
            db.query(AgentDecision.id)
            .filter(
                AgentDecision.application_id == CandidateApplication.id,
                AgentDecision.status == "pending",
            )
            .exists()
        )
        rows = (
            db.query(CandidateApplication.id)
            .join(Role, Role.id == CandidateApplication.role_id)
            .filter(
                # Agent on OR off — a deterministic pre-screen reject is surfaced
                # for every role (the card path needs no agent reasoning).
                Role.deleted_at.is_(None),
                CandidateApplication.application_outcome == "open",
                # Pre-screen-only: once a full cv_match score exists the agent
                # owns the verdict (matches evaluate_auto_reject_decision).
                CandidateApplication.cv_match_score.is_(None),
                # GENUINE pre-screen only: the recommendation/score columns can
                # be stamped by a cv_match snapshot refresh without a pre-screen
                # ever running; pre_screen_run_at is set only by the engine.
                CandidateApplication.pre_screen_run_at.isnot(None),
                or_(
                    and_(
                        CandidateApplication.pre_screen_score_100.isnot(None),
                        CandidateApplication.pre_screen_score_100 < effective_cutoff,
                    ),
                    CandidateApplication.pre_screen_recommendation == "Below threshold",
                ),
                ~has_pending,
            )
            .order_by(CandidateApplication.id.asc())
            .limit(int(cap))
            .all()
        )
        for (application_id,) in rows:
            try:
                run_application_auto_reject.delay(int(application_id))
                dispatched.append(int(application_id))
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "pre_screen_reject_sweep dispatch failed application_id=%s",
                    application_id,
                )
    except Exception:
        logger.exception("pre_screen_reject_sweep failed")
        return {"status": "error", "dispatched": len(dispatched)}
    finally:
        db.close()
    logger.info("pre_screen_reject_sweep dispatched %d auto-reject task(s)", len(dispatched))
    return {"status": "ok", "dispatched_count": len(dispatched), "application_ids": dispatched}


@celery_app.task(
    name="app.tasks.agent_tasks.agent_cohort_tick_role",
    bind=True,
    max_retries=0,
    soft_time_limit=AGENT_CYCLE_SOFT_LIMIT_S,
    time_limit=AGENT_CYCLE_HARD_LIMIT_S,
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

        # Phase 1.5: keep the deterministic pre-screen reject queue aligned
        # with the role's current threshold. Pure DB, no LLM, idempotent —
        # after it converges each tick is a near-no-op. This is what makes a
        # threshold change "stick" across every surface without a manual
        # backfill: the tick discards reject cards the current cutoff no
        # longer justifies and emits any that are now missing. Runs before
        # the no-op early-exit so the queue self-heals even on ticks where
        # the LLM cycle has nothing to do. Failures never abort the tick.
        try:
            from ..services.pre_screen_decision_emitter import (
                reconcile_pre_screen_reject_decisions,
                retract_advances_below_threshold,
            )
            from ..services.pre_screening_service import resolved_auto_reject_config

            _thr = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
            # Retract stale advances below the cutoff first, then let the reject
            # reconcile emit the matching skip_assessment_reject in their place.
            retract_advances_below_threshold(
                db,
                role=role,
                organization_id=int(role.organization_id),
                threshold=_thr,
            )
            reconcile_pre_screen_reject_decisions(
                db,
                role=role,
                organization_id=int(role.organization_id),
                threshold=_thr,
            )
        except Exception:
            logger.exception(
                "pre-screen reject reconcile failed in cohort tick role_id=%s", role_id
            )
            db.rollback()

        # Phase 1.6: correct stale "Below threshold" *display* labels left by
        # the old hard-coded <50 rule (relax-only — only un-flags candidates
        # now above the role's cutoff; never introduces a new reject label).
        # Pairs with the decision reconcile above so the verdict and the
        # displayed recommendation agree. Idempotent; a no-op once converged.
        try:
            from ..services.pre_screen_decision_emitter import (
                rederive_pre_screen_recommendations,
            )

            rederive_pre_screen_recommendations(db, role_id=role_id)
        except Exception:
            logger.exception(
                "pre-screen recommendation re-derive failed in cohort tick role_id=%s",
                role_id,
            )
            db.rollback()

        # Phase 1.7: deterministic bulk decisioning. The policy verdict is
        # deterministic, so every undecided pre-screen-pass scored OPEN
        # candidate gets its verdict queued here (reject below the
        # threshold; send_assessment / advance above it) — no LLM, no
        # per-cycle decision cap. This is what makes "every candidate has a
        # decision" true and clears large cohorts in one or two ticks
        # instead of hundreds of LLM cycles. Runs before the no-op
        # early-exit so it self-heals regardless of the LLM cycle, and
        # before run_cycle so find_apps_in_state excludes the now-pending
        # apps (no double-queue). Failures never abort the tick.
        bulk_decided = None
        try:
            from ..services.bulk_decision_service import decide_role_cohort

            bulk_decided = decide_role_cohort(db, role=role)
        except Exception:
            logger.exception(
                "bulk decisioning failed in cohort tick role_id=%s", role_id
            )
            db.rollback()

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
                "bulk_decided": bulk_decided,
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

        from ..platform.config import settings
        from ..models.candidate_application import CandidateApplication
        from ..services.cv_score_orchestrator import enqueue_score
        from ..services.pre_screening_service import PRE_SCREEN_ERROR_BACKOFF

        backoff_cutoff = datetime.now(timezone.utc) - PRE_SCREEN_ERROR_BACKOFF
        # Re-screen is only worthwhile when the candidate uploaded a newer
        # CV after the last pre-screen run.
        fresh_cv = and_(
            CandidateApplication.cv_uploaded_at.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
            CandidateApplication.cv_uploaded_at > CandidateApplication.pre_screen_run_at,
        )
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
                    CandidateApplication.pre_screen_score_100 >= settings.PRE_SCREEN_THRESHOLD,
                    CandidateApplication.pre_screen_error_reason.isnot(None),
                    fresh_cv,
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

        # Anything to ask or answer about the role? survey_role_state
        # emits the list under ``open_recruiter_questions`` — reading the
        # wrong key here treated roles with unresolved recruiter questions
        # as no-ops and skipped their cycle.
        if survey.get("open_recruiter_questions") or survey.get("intent_gaps"):
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
    soft_time_limit=AGENT_CYCLE_SOFT_LIMIT_S,
    time_limit=AGENT_CYCLE_HARD_LIMIT_S,
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
# Backstop for rows left in 'running' after a worker is force-killed by the
# Celery hard time_limit (360s) — or after any crash the time limit can't
# catch. Set just above the 6-min hard limit so a row is reaped ~1 min
# after its task is killed, instead of the old 10-min wait. Cycle work
# tops out ~171s in prod, so 7 min never touches a healthy in-flight run.
STUCK_RUN_TIMEOUT_MINUTES = 7


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


# Stale pending decisions. BUG-2: ``expired`` is a valid AgentDecision status
# but nothing ever set it — a pending verdict could sit in the recruiter's Hub
# queue forever with no SLA. ``agent_expire_stuck_runs`` above reaps stuck
# AgentRun rows, NOT stale decisions. This sweep ages out stale *pending*
# decisions:
#   * normal verdicts older than DECISION_PENDING_SLA_DAYS → status='expired'
#   * escalations (``escalate_low_confidence``) are NEVER silently expired —
#     an escalation is a "human MUST decide" signal, so we re-surface
#     (re-prioritise) it instead, throttled to once per window so a
#     long-ignored escalation doesn't spam the activity feed.
# Snoozed rows (recruiter explicitly parked them) and non-pending rows are
# left alone.
DECISION_PENDING_SLA_DAYS = 14
ESCALATION_REESCALATE_AFTER_DAYS = 3


@celery_app.task(
    name="app.tasks.agent_tasks.agent_expire_stale_decisions",
    bind=True,
    max_retries=0,
)
def agent_expire_stale_decisions(self) -> dict:
    """Age out stale pending AgentDecisions; re-surface stale escalations.

    No-op when nothing is stale. Idempotent — re-running only touches rows
    still past their SLA, and the re-escalation event is keyed per window so
    a second run in the same window doesn't duplicate it.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_

    from ..models.agent_decision import AgentDecision
    from ..models.candidate_application_event import CandidateApplicationEvent
    from ..platform.database import SessionLocal

    now = datetime.now(timezone.utc)
    expiry_cutoff = now - timedelta(days=DECISION_PENDING_SLA_DAYS)
    escalation_cutoff = now - timedelta(days=ESCALATION_REESCALATE_AFTER_DAYS)

    db = SessionLocal()
    expired_ids: list[int] = []
    reescalated_ids: list[int] = []
    try:
        # A pending row is "live in the queue" only when it isn't snoozed into
        # the future — a snooze is the recruiter explicitly parking it.
        not_snoozed = or_(
            AgentDecision.snoozed_until.is_(None),
            AgentDecision.snoozed_until <= now,
        )

        # 1. Expire stale non-escalation verdicts.
        stale = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.status == "pending",
                AgentDecision.decision_type != "escalate_low_confidence",
                AgentDecision.created_at < expiry_cutoff,
                not_snoozed,
            )
            .all()
        )
        for decision in stale:
            decision.status = "expired"
            decision.resolved_at = now
            decision.resolution_note = (
                decision.resolution_note
                or f"Expired — no recruiter action within {DECISION_PENDING_SLA_DAYS}d SLA"
            )
            expired_ids.append(int(decision.id))

        # 2. Re-surface stale escalations rather than expiring them. Bump them
        # back into view and record a re-escalation event, throttled to once
        # per ESCALATION_REESCALATE_AFTER_DAYS window via a window-bucketed
        # idempotency key so an ignored escalation doesn't spam the feed on
        # every sweep.
        stale_escalations = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.status == "pending",
                AgentDecision.decision_type == "escalate_low_confidence",
                AgentDecision.created_at < escalation_cutoff,
            )
            .all()
        )
        for decision in stale_escalations:
            created = decision.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = (now - created).days if created is not None else 0
            bucket = age_days // ESCALATION_REESCALATE_AFTER_DAYS
            idem = f"agent_decision_reescalated:{int(decision.id)}:{bucket}"
            already = (
                db.query(CandidateApplicationEvent.id)
                .filter(
                    CandidateApplicationEvent.application_id == int(decision.application_id),
                    CandidateApplicationEvent.idempotency_key == idem,
                )
                .first()
            )
            if already is not None:
                continue  # already re-escalated this window
            # Un-snooze so it's visible, and stamp a fresh activity event so
            # the Hub re-surfaces it for recruiter review.
            decision.snoozed_until = None
            db.add(
                CandidateApplicationEvent(
                    application_id=int(decision.application_id),
                    organization_id=int(decision.organization_id),
                    event_type="agent_decision_reescalated",
                    actor_type="system",
                    actor_id=None,
                    reason=(
                        f"Escalation unresolved after {age_days}d — re-prioritised "
                        "for recruiter review"
                    ),
                    idempotency_key=idem,
                    event_metadata={
                        "decision_id": int(decision.id),
                        "age_days": age_days,
                    },
                )
            )
            reescalated_ids.append(int(decision.id))

        if expired_ids or reescalated_ids:
            db.commit()
            logger.warning(
                "agent_expire_stale_decisions expired %d decision(s), re-escalated %d: "
                "expired=%s reescalated=%s",
                len(expired_ids), len(reescalated_ids), expired_ids, reescalated_ids,
            )
    except Exception:
        db.rollback()
        logger.exception("agent_expire_stale_decisions failed")
        return {"status": "error"}
    finally:
        db.close()
    return {
        "status": "ok",
        "expired_count": len(expired_ids),
        "expired_decision_ids": expired_ids,
        "reescalated_count": len(reescalated_ids),
        "reescalated_decision_ids": reescalated_ids,
    }
