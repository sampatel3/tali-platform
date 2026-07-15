"""Celery tasks for the autonomous recruiting agent.

The actual cycle work lives in ``app.agent_runtime.orchestrator``;
these are thin wrappers that own the DB session lifecycle and Celery
plumbing.

Triggers:
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


def _mark_agent_tick_ready(db, *, role) -> None:
    """Persist the worker acknowledgement for a successful cohort tick."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    role.agent_last_run_at = now
    role.agent_bootstrap_status = "ready"
    role.agent_bootstrap_error = None
    role.agent_bootstrap_completed_at = now
    db.add(role)
    db.commit()


def _mark_agent_bootstrap_failed(
    db,
    *,
    role,
    detail: str,
    pause: bool = True,
    pause_reason: str | None = None,
    audit_control_change: bool = False,
) -> None:
    """Persist an honest terminal bootstrap state and optionally fail closed."""
    from datetime import datetime, timezone

    from ..agent_runtime import budget_guard

    now = datetime.now(timezone.utc)
    message = str(detail or "agent bootstrap failed")[:2000]
    if audit_control_change:
        from ..services.role_change_audit import capture_role_change_snapshot

        audit_before = capture_role_change_snapshot(role)
        audit_from_version = int(role.version or 1)
    role.agent_bootstrap_status = "failed"
    role.agent_bootstrap_error = message
    role.agent_bootstrap_completed_at = now
    if pause and role.agent_paused_at is None:
        budget_guard.pause_role(
            db,
            role=role,
            reason=(
                str(pause_reason)[:2000]
                if pause_reason
                else f"agent bootstrap failed after retries: {message}"[:2000]
            ),
        )
    if audit_control_change:
        from ..services.role_change_audit import (
            ROLE_CHANGE_ACTION_AGENT_PAUSED,
            add_role_change_event,
        )
        from ..services.role_concurrency import bump_role_version

        audit_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            actor_user_id=None,
            from_version=audit_from_version,
            to_version=audit_to_version,
            reason=str(pause_reason or message)[:2000],
        )
    db.add(role)
    db.commit()


def _retry_or_fail_cohort_bootstrap(
    task,
    *,
    db,
    role,
    exc: Exception,
    activation: bool,
    dispatch_role_version: int,
) -> None:
    """Retry a failed tick; on final activation failure pause the role.

    Celery considers a task successful when it merely returns an error-shaped
    dict. Raising ``retry`` keeps monitoring honest. When the bootstrap was the
    recruiter's explicit Turn-on action, exhausting retries also moves the role
    to a durable failed/paused state instead of leaving it apparently on.
    """
    role_id = int(role.id)
    dispatched_version = int(dispatch_role_version)
    db.rollback()
    retries = int(getattr(task.request, "retries", 0) or 0)
    max_retries = int(getattr(task, "max_retries", 3) or 3)
    if activation and retries >= max_retries:
        from ..models.role import Role

        current = (
            db.query(Role)
            .filter(Role.id == role_id, Role.deleted_at.is_(None))
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
        if (
            current is not None
            and int(current.version or 1) == dispatched_version
            and bool(current.agentic_mode_enabled)
            and current.agent_paused_at is None
        ):
            _mark_agent_bootstrap_failed(
                db,
                role=current,
                detail=str(exc),
                pause=True,
                audit_control_change=True,
            )
        else:
            # The failed worker belongs to an older shared-role revision (or
            # the role was deleted/turned off/paused). Never let retry
            # exhaustion overwrite that newer recruiter action.
            current_version = (
                int(current.version or 1) if current is not None else None
            )
            db.rollback()
            logger.info(
                "skipped stale agent bootstrap failure compensation "
                "role_id=%s dispatch_version=%s current_version=%s",
                role_id,
                dispatched_version,
                current_version,
            )
        logger.error(
            "agent activation bootstrap exhausted retries role_id=%s error=%s",
            role_id,
            exc,
        )
        raise exc
    countdown = min(15 * 60, 60 * (2**retries))
    # A deferred activation learns its authoritative revision only after the
    # first worker atomically switches the role on. Persist that immutable
    # token into Celery's retry signature so later attempts cannot silently
    # recapture a newer revision and compensate it as though it were theirs.
    retry_kwargs = dict(getattr(task.request, "kwargs", {}) or {})
    request_args = tuple(getattr(task.request, "args", ()) or ())
    if len(request_args) < 4:
        retry_kwargs["dispatch_role_version"] = dispatched_version
    raise task.retry(exc=exc, countdown=countdown, kwargs=retry_kwargs)


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
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal

    enqueued: list[int] = []
    skipped_paused = 0
    db = SessionLocal()
    try:
        roles = (
            db.query(Role.id)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
                Role.agent_paused_at.is_(None),
                Organization.agent_workspace_paused_at.is_(None),
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

    Runs with trigger="cron" and no application_id — the agent
    decides what's worth surfacing
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
    from ..services.role_execution_guard import (
        automatic_role_action_block_reason,
    )

    db = SessionLocal()
    try:
        role = (
            db.query(Role)
            .filter(Role.id == role_id, Role.deleted_at.is_(None))
            .first()
        )
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
        role_block = automatic_role_action_block_reason(role, db=db)
        if role_block:
            return {
                "status": "skipped",
                "reason": "role_not_runnable",
                "detail": role_block,
                "role_id": role_id,
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
    """Phase 7 cohort planner: every 60 min, fan a tick to each
    agent-enabled, non-paused role.

    Replaces the per-application event trigger. The orchestrator
    surveys cohort state itself via ``survey_role_state`` and decides
    what's worth doing this cycle. With agents off, this is a no-op
    sweep — paused / disabled roles fall through.
    """
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal

    enqueued: list[int] = []
    db = SessionLocal()
    try:
        roles = (
            db.query(Role.id, Role.version)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
                Role.agent_paused_at.is_(None),
                Organization.agent_workspace_paused_at.is_(None),
            )
            .all()
        )
        for role_id, role_version in roles:
            agent_cohort_tick_role.delay(
                int(role_id),
                dispatch_role_version=int(role_version or 1),
            )
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


AGENT_RECOVERY_SWEEP_CAP = 500


@celery_app.task(
    name="app.tasks.agent_tasks.agent_recovery_sweep",
    bind=True,
    max_retries=0,
)
def agent_recovery_sweep(self, cap: int = AGENT_RECOVERY_SWEEP_CAP) -> dict:
    """Automatically release *system* holds once their cause is healthy.

    Budget month rollover, a credit top-up, or restored worker/provider
    health should not require a recruiter to notice a pause and click Resume.
    The shared resume guard re-runs the full budget + production-readiness
    contract; recruiter-authored pauses are never touched. A recovered role
    receives an immediate cohort tick instead of waiting for the hourly sweep.
    """
    from sqlalchemy import or_

    from ..agent_runtime import budget_guard
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.role_change_audit import (
        ROLE_CHANGE_ACTION_AGENT_RESUMED,
        add_role_change_event,
        capture_role_change_snapshot,
    )
    from ..services.role_concurrency import bump_role_version

    resumed: list[int] = []
    dispatch_failed: list[int] = []
    checked = 0
    db = SessionLocal()
    try:
        roles = (
            db.query(Role.id)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
                Role.agent_paused_at.isnot(None),
                or_(
                    Role.agent_paused_reason.is_(None),
                    Role.agent_paused_reason != budget_guard.MANUAL_PAUSE_REASON,
                ),
            )
            .order_by(Role.agent_paused_at.asc())
            .limit(max(1, min(int(cap), AGENT_RECOVERY_SWEEP_CAP)))
            .all()
        )
        for (candidate_role_id,) in roles:
            checked += 1
            try:
                # Re-lock and re-check each candidate. Multiple beat workers
                # may overlap; only one may own the paused revision and emit
                # its recovery dispatch.
                role = (
                    db.query(Role)
                    .filter(
                        Role.id == int(candidate_role_id),
                        Role.agentic_mode_enabled.is_(True),
                        Role.deleted_at.is_(None),
                        Role.agent_paused_at.isnot(None),
                        or_(
                            Role.agent_paused_reason.is_(None),
                            Role.agent_paused_reason
                            != budget_guard.MANUAL_PAUSE_REASON,
                        ),
                    )
                    .populate_existing()
                    .with_for_update(of=Role)
                    .one_or_none()
                )
                if role is None:
                    db.rollback()
                    continue
                audit_before = capture_role_change_snapshot(role)
                audit_from_version = int(role.version or 1)
                if not budget_guard.resume_if_under_budget(
                    db, role=role, explicit=False
                ):
                    db.rollback()
                    continue
                dispatched_role_id = int(role.id)
                dispatched_role_version = bump_role_version(role)
                add_role_change_event(
                    db,
                    role=role,
                    before=audit_before,
                    action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
                    actor_user_id=None,
                    from_version=audit_from_version,
                    to_version=dispatched_role_version,
                    reason="automatic recovery after system hold cleared",
                )
                # Commit the guarded recovery before dispatch. If broker
                # delivery fails, compensate below back to a durable hold.
                db.commit()
                try:
                    agent_cohort_tick_role.delay(
                        dispatched_role_id,
                        activation=False,
                        dispatch_role_version=dispatched_role_version,
                    )
                except Exception:
                    logger.exception(
                        "agent recovery dispatch failed role_id=%s",
                        dispatched_role_id,
                    )
                    current = (
                        db.query(Role)
                        .filter(
                            Role.id == dispatched_role_id,
                            Role.deleted_at.is_(None),
                        )
                        .populate_existing()
                        .with_for_update(of=Role)
                        .one_or_none()
                    )
                    if (
                        current is not None
                        and int(current.version or 1)
                        == dispatched_role_version
                        and bool(current.agentic_mode_enabled)
                        and current.agent_paused_at is None
                    ):
                        _mark_agent_bootstrap_failed(
                            db,
                            role=current,
                            detail="agent recovery dispatch failed",
                            pause=True,
                            pause_reason="agent recovery dispatch failed",
                            audit_control_change=True,
                        )
                    else:
                        current_version = (
                            int(current.version or 1)
                            if current is not None
                            else None
                        )
                        db.rollback()
                        logger.info(
                            "skipped stale agent recovery compensation "
                            "role_id=%s dispatch_version=%s current_version=%s",
                            dispatched_role_id,
                            dispatched_role_version,
                            current_version,
                        )
                    dispatch_failed.append(dispatched_role_id)
                    continue
                resumed.append(dispatched_role_id)
            except Exception:
                db.rollback()
                logger.exception(
                    "agent recovery check failed role_id=%s",
                    candidate_role_id,
                )
    except Exception:
        db.rollback()
        logger.exception("agent_recovery_sweep failed")
        return {
            "status": "error",
            "checked": checked,
            "resumed": resumed,
            "dispatch_failed": dispatch_failed,
        }
    finally:
        db.close()
    return {
        "status": "ok",
        "checked": checked,
        "resumed_count": len(resumed),
        "role_ids": resumed,
        "dispatch_failed": dispatch_failed,
    }


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
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=AGENT_CYCLE_SOFT_LIMIT_S,
    time_limit=AGENT_CYCLE_HARD_LIMIT_S,
)
def agent_cohort_tick_role(
    self,
    role_id: int,
    activation: bool = False,
    activation_intent_id: str | None = None,
    dispatch_role_version: int | None = None,
    dispatch_workspace_version: int | None = None,
) -> dict:
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
    newly enqueued scores. Score completion now materializes its decision
    immediately; the next 60-minute tick is only a recovery backstop.
    """
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.role_execution_guard import (
        automatic_role_action_block_reason,
    )
    from ..services.workspace_agent_control import (
        workspace_agent_control_snapshot,
    )

    db = SessionLocal()
    try:
        if activation_intent_id:
            from ..services.role_activation_intent import (
                complete_role_activation_intent,
            )

            activation_result = complete_role_activation_intent(
                db,
                role_id=int(role_id),
                request_id=str(activation_intent_id),
                worker_task_id=str(getattr(self.request, "id", "") or ""),
            )
            if activation_result.get("status") not in {
                "activated",
                "already_activated",
            }:
                return {
                    "status": "skipped",
                    "reason": f"activation_{activation_result.get('status', 'inactive')}",
                    "role_id": int(role_id),
                }
            # The helper commits the atomic OFF->ON transition. Expire any
            # identity-map state before this normal cohort worker continues.
            db.expire_all()
        role = (
            db.query(Role)
            .filter(Role.id == role_id, Role.deleted_at.is_(None))
            .first()
        )
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        workspace_paused, workspace_version = workspace_agent_control_snapshot(
            db,
            organization_id=int(role.organization_id),
        )
        if workspace_paused:
            return {
                "status": "skipped",
                "reason": "workspace_paused",
                "role_id": role_id,
            }
        if (
            dispatch_workspace_version is not None
            and int(dispatch_workspace_version) != int(workspace_version)
        ):
            return {
                "status": "skipped",
                "reason": "stale_workspace_control",
                "role_id": role_id,
                "dispatch_workspace_version": int(dispatch_workspace_version),
                "workspace_control_version": int(workspace_version),
            }
        role_block = automatic_role_action_block_reason(role, db=db)
        if role_block:
            return {
                "status": "skipped",
                "reason": "not_eligible",
                "detail": role_block,
                "role_id": role_id,
            }
        # Both false→true activation and pause→resume stamp ``starting`` before
        # dispatch.  The Celery argument distinguishes the larger activation
        # scoring cap; the persisted state distinguishes any bootstrap that
        # must fail closed after retry exhaustion.
        bootstrap = bool(
            activation or getattr(role, "agent_bootstrap_status", None) == "starting"
        )
        # Direct activation/resume callers pass the revision they committed.
        # Deferred activation cannot know its post-transition version until
        # this worker completes the intent, so capture it exactly once here
        # and thread it through every Celery retry.
        if dispatch_role_version is None:
            dispatch_role_version = int(role.version or 1)

        # B4: concurrent LLM-cycle guard. If a previous cycle for this role is
        # still running, don't start another paid deliberation. A bootstrap is
        # the exception for Phase 1 only: Turn on promises an immediate,
        # 500-candidate scoring drain, and enqueue_score is idempotent, so an
        # old AgentRun must not make activation silently skip that work. The
        # bootstrap returns after Phase 1 and never starts a second LLM cycle.
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
        if in_flight is not None and not bootstrap:
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
        try:
            auto_scored = _auto_enqueue_scoring(
                db,
                role=role,
                limit=(ACTIVATION_AUTO_SCORE_CAP if activation else AUTO_SCORE_PER_TICK_CAP),
                strict=bootstrap,
            )
        except Exception as exc:
            logger.exception(
                "activation score bootstrap failed role_id=%s", role_id
            )
            _retry_or_fail_cohort_bootstrap(
                self,
                db=db,
                role=role,
                exc=exc,
                activation=bootstrap,
                dispatch_role_version=int(dispatch_role_version),
            )
        if role.agent_paused_at is not None:
            return {
                "status": "paused",
                "reason": role.agent_paused_reason,
                "role_id": role_id,
                "auto_scored_enqueued": auto_scored,
            }
        if in_flight is not None:
            # Phase 1 completed successfully, while the existing AgentRun is
            # already handling Phase 2. That is a complete bootstrap handoff.
            _mark_agent_tick_ready(db, role=role)
            return {
                "status": "skipped",
                "reason": "already_running",
                "role_id": role_id,
                "in_flight_run_id": int(in_flight[0]),
                "auto_scored_enqueued": auto_scored,
            }

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
        except Exception as exc:
            logger.exception(
                "pre-screen reject reconcile failed in cohort tick role_id=%s", role_id
            )
            db.rollback()
            if bootstrap:
                _retry_or_fail_cohort_bootstrap(
                    self,
                    db=db,
                    role=role,
                    exc=exc,
                    activation=True,
                    dispatch_role_version=int(dispatch_role_version),
                )

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
        except Exception as exc:
            logger.exception(
                "pre-screen recommendation re-derive failed in cohort tick role_id=%s",
                role_id,
            )
            db.rollback()
            if bootstrap:
                _retry_or_fail_cohort_bootstrap(
                    self,
                    db=db,
                    role=role,
                    exc=exc,
                    activation=True,
                    dispatch_role_version=int(dispatch_role_version),
                )

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
            _mark_agent_tick_ready(db, role=role)
            return {
                "status": "skipped",
                "reason": "no_actionable_work",
                "role_id": role_id,
                "auto_scored_enqueued": auto_scored,
                "bulk_decided": bulk_decided,
            }

        # Phase 2.5: redundant-cycle gate. The bulk pass above already queued
        # every CLEAR verdict (no LLM); run_cycle only adds value when it can
        # resolve a NEW escalation/question. If the last LLM cycle for this role
        # succeeded with ZERO decisions and nothing in the cohort changed since,
        # re-running would again yield nothing — skip it. A force-run backstop
        # (max staleness) guarantees a real cycle at least every N hours, so a
        # missed yield is delayed (≤N h), never lost. Backtest (30d real runs):
        # ~half the cron LLM cycles avoided, zero decisions lost.
        from ..platform.config import settings as _settings

        _gate_mode = (_settings.AGENT_COHORT_GATE_MODE or "off").strip().lower()
        if _gate_mode in ("shadow", "on"):
            _gate = _redundant_cycle_gate(db, role=role)
            if _gate.get("would_skip"):
                if _gate_mode == "on":
                    logger.info("cohort gate SKIP role_id=%s (%s)", role_id, _gate["reason"])
                    _mark_agent_tick_ready(db, role=role)
                    return {
                        "status": "skipped",
                        "reason": "redundant_cycle",
                        "role_id": role_id,
                        "auto_scored_enqueued": auto_scored,
                        "bulk_decided": bulk_decided,
                    }
                logger.info(
                    "cohort gate SHADOW would-skip role_id=%s (%s)", role_id, _gate["reason"]
                )

        try:
            run = run_cycle(
                db,
                role=role,
                trigger="cron",
                application_id=None,
            )
            db.commit()
        except Exception as exc:
            logger.exception("agent_cohort_tick_role failed role_id=%s", role_id)
            _retry_or_fail_cohort_bootstrap(
                self,
                db=db,
                role=role,
                exc=exc,
                activation=bootstrap,
                dispatch_role_version=int(dispatch_role_version),
            )
        if str(run.status) == "aborted" and str(run.error or "") in {
            "workspace_paused_before_cycle",
            "workspace_paused_during_cycle",
            "workspace_control_changed_during_cycle",
        }:
            # The workspace overlay is not a role bootstrap failure. Preserve
            # the role's desired ON/local-pause state; Resume workspace will
            # dispatch a fresh tick carrying the new workspace generation.
            return {
                "status": "skipped",
                "reason": str(run.error),
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "auto_scored_enqueued": auto_scored,
            }
        if str(run.status) in {"failed", "aborted"}:
            _retry_or_fail_cohort_bootstrap(
                self,
                db=db,
                role=role,
                exc=RuntimeError(
                    f"agent cycle {run.status}: {run.error or 'no completion acknowledgement'}"
                ),
                activation=bootstrap,
                dispatch_role_version=int(dispatch_role_version),
            )
        if str(run.status) == "budget_paused":
            _mark_agent_bootstrap_failed(
                db,
                role=role,
                detail=str(run.error or "monthly role budget reached"),
                pause=False,
            )
            return {
                "status": "paused",
                "role_id": role_id,
                "agent_run_id": int(run.id),
                "run_status": str(run.status),
                "auto_scored_enqueued": auto_scored,
            }
        _mark_agent_tick_ready(db, role=role)
        return {
            "status": "ok",
            "role_id": role_id,
            "agent_run_id": int(run.id),
            "run_status": str(run.status),
            "decisions_emitted": int(run.decisions_emitted),
            "auto_scored_enqueued": auto_scored,
        }
    finally:
        db.close()


# How many candidates the auto-scoring helper queues per cohort tick.
# Cohort ticks fire every 60 min. 50 candidates × 24 ticks = 1,200/day
# per role — enough to keep up with steady-state Workable sync without
# burst-firing thousands of API calls on a freshly-enabled role with a
# long backlog. The first tick after agent activation gets a higher cap
# via ``ACTIVATION_AUTO_SCORE_CAP`` (one-shot drain).
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
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import and_, func, not_, or_

        from ..platform.config import settings
        from ..models.candidate_application import CandidateApplication
        from ..models.cv_score_job import (
            SCORE_JOB_PENDING,
            SCORE_JOB_RUNNING,
            CvScoreJob,
        )
        from ..services.cv_score_orchestrator import enqueue_score
        from ..services.pre_screening_service import PRE_SCREEN_ERROR_BACKOFF
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

        backoff_cutoff = datetime.now(timezone.utc) - PRE_SCREEN_ERROR_BACKOFF
        # Re-screen is only worthwhile when the candidate uploaded a newer
        # CV after the last pre-screen run.
        fresh_cv = and_(
            CandidateApplication.cv_uploaded_at.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
            CandidateApplication.cv_uploaded_at > CandidateApplication.pre_screen_run_at,
        )
        active_score_job = (
            db.query(CvScoreJob.id)
            .filter(
                CvScoreJob.application_id == CandidateApplication.id,
                CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
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
                role.agent_bootstrap_error = str(exc)
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


def _redundant_cycle_gate(db, *, role) -> dict:
    """Would re-running the autonomous LLM cycle for ``role`` yield nothing?

    Returns ``{"would_skip": bool, "reason": str}``. ``would_skip`` is True only
    when the most recent CRON LLM cycle SUCCEEDED with ZERO decisions, nothing
    in the cohort has changed since it started, and we're still within the
    force-run staleness window. The bulk-deterministic pass runs every tick
    regardless, so a skip only suppresses redundant re-deliberation of an
    unchanged cohort — a yield this gate skips is delayed until the next forced
    cycle (≤ ``AGENT_COHORT_GATE_MAX_STALENESS_HOURS``), never lost.

    Pure read; never raises (any error → ``would_skip=False`` so the cycle runs).
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_

    from ..models.agent_run import AgentRun
    from ..models.candidate_application import CandidateApplication
    from ..platform.config import settings

    try:
        last = (
            db.query(AgentRun)
            .filter(
                AgentRun.role_id == int(role.id),
                AgentRun.trigger == "cron",
                AgentRun.model_version != "bulk-deterministic",
            )
            .order_by(AgentRun.started_at.desc())
            .first()
        )
        if last is None:
            return {"would_skip": False, "reason": "no_prior_llm_cycle"}
        if last.status != "succeeded" or (last.decisions_emitted or 0) > 0:
            return {"would_skip": False, "reason": "prior_yielded_or_failed"}
        started = last.started_at
        if started is None:
            return {"would_skip": False, "reason": "no_started_at"}
        # Time math needs an aware value (SQLite returns naive → assume UTC); the
        # SQL change-comparison below keeps the RAW `started` so it matches the
        # column's stored tz-ness (naive↔naive on SQLite, aware↔aware on PG).
        started_aware = started if started.tzinfo else started.replace(tzinfo=timezone.utc)

        stale_h = int(getattr(settings, "AGENT_COHORT_GATE_MAX_STALENESS_HOURS", 4) or 4)
        if datetime.now(timezone.utc) - started_aware >= timedelta(hours=stale_h):
            return {"would_skip": False, "reason": "force_run_stale"}

        # Any cohort change since the last cycle started? (new score, stage move,
        # outcome flip, recruiter edit reflected on the app) → there may be new
        # work to deliberate, so run.
        changed = (
            db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.role_id == int(role.id),
                or_(
                    CandidateApplication.updated_at > started,
                    CandidateApplication.score_cached_at > started,
                    CandidateApplication.pipeline_stage_updated_at > started,
                    CandidateApplication.application_outcome_updated_at > started,
                ),
            )
            .first()
            is not None
        )
        if changed:
            return {"would_skip": False, "reason": "cohort_changed"}

        role_updated = getattr(role, "updated_at", None)
        if role_updated is not None and role_updated > started:
            return {"would_skip": False, "reason": "role_changed"}

        return {"would_skip": True, "reason": "redundant_unchanged_zero_yield"}
    except Exception:  # noqa: BLE001 — gate is best-effort; never block a cycle
        logger.exception("redundant_cycle_gate failed role_id=%s", getattr(role, "id", "?"))
        return {"would_skip": False, "reason": "gate_error"}


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

    Manual urgency does not override the shared role's power state.  A
    separate, side-effect-free preview facility can be introduced later; this
    production worker must never spend or queue recommendations while another
    recruiter has deliberately turned the agent off.
    """
    from ..agent_runtime.orchestrator import run_cycle
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.role_execution_guard import automatic_role_action_block_reason

    db = SessionLocal()
    try:
        role = (
            db.query(Role)
            .filter(Role.id == role_id, Role.deleted_at.is_(None))
            .first()
        )
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        if not bool(role.agentic_mode_enabled):
            return {
                "status": "skipped",
                "reason": "agent_disabled",
                "role_id": role_id,
            }
        if role.agent_paused_at is not None:
            return {
                "status": "skipped",
                "reason": "agent_paused",
                "role_id": role_id,
                "paused_reason": role.agent_paused_reason,
            }
        role_block = automatic_role_action_block_reason(role, db=db)
        if role_block:
            return {
                "status": "skipped",
                "reason": "workspace_paused"
                if role_block == "workspace agent is paused"
                else "role_not_runnable",
                "detail": role_block,
                "role_id": role_id,
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

    from ..agent_chat.events import try_post_agent_run_event
    from ..models.agent_run import AgentRun
    from ..models.role import Role
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
        role_by_id = {
            int(role.id): role
            for role in (
                db.query(Role)
                .filter(Role.id.in_({int(run.role_id) for run in stuck} or {0}))
                .all()
            )
        }
        for run in stuck:
            run.status = "failed"
            run.error = (
                run.error
                or f"watchdog: still running after {STUCK_RUN_TIMEOUT_MINUTES}m — worker likely crashed mid-cycle"
            )
            run.finished_at = now
            expired_ids.append(int(run.id))
            role = role_by_id.get(int(run.role_id))
            if role is not None:
                try_post_agent_run_event(db, role=role, run=run)
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


# Event publication reconciliation. AgentRun remains the source of truth; this
# sweep makes the direct same-transaction chat write eventually reliable if a
# transient notification-only failure was contained by its savepoint. The
# message source-key unique constraint makes retries and overlapping sweeps safe.
@celery_app.task(
    name="app.tasks.agent_tasks.agent_publish_terminal_run_events",
    bind=True,
    max_retries=0,
)
def agent_publish_terminal_run_events(self, limit: int = 200) -> dict:
    """Backfill missing failure/budget event cards from terminal AgentRuns."""

    from datetime import datetime, timedelta, timezone

    from ..agent_chat.events import try_post_agent_run_event
    from ..models.agent_run import AgentRun
    from ..models.role import Role
    from ..platform.database import SessionLocal

    bounded_limit = max(1, min(int(limit or 200), 500))
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    db = SessionLocal()
    attempted = 0
    emitted = 0
    try:
        runs = (
            db.query(AgentRun)
            .filter(
                AgentRun.status.in_(("failed", "aborted", "budget_paused")),
                AgentRun.finished_at.isnot(None),
                AgentRun.finished_at >= cutoff,
            )
            .order_by(AgentRun.finished_at.desc(), AgentRun.id.desc())
            .limit(bounded_limit)
            .all()
        )
        role_by_id = {
            int(role.id): role
            for role in (
                db.query(Role)
                .filter(
                    Role.id.in_({int(run.role_id) for run in runs} or {0}),
                    Role.deleted_at.is_(None),
                )
                .all()
            )
        }
        for run in runs:
            role = role_by_id.get(int(run.role_id))
            if role is None:
                continue
            attempted += 1
            if try_post_agent_run_event(db, role=role, run=run) is not None:
                emitted += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("agent_publish_terminal_run_events failed")
        return {"status": "error", "attempted": attempted, "emitted": emitted}
    finally:
        db.close()
    return {"status": "ok", "attempted": attempted, "emitted": emitted}


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
    from ..models.candidate_application import CandidateApplication
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

        # 1. Expire stale non-escalation verdicts — but ONLY for candidates
        # whose application has already moved on (outcome != 'open'). A
        # deterministic verdict on a still-OPEN candidate stays correct until
        # the recruiter acts (the score hasn't changed, so the recommendation
        # hasn't either) — expiring it silently stranded the candidate as "not
        # yet decided", which is exactly the limbo it must never produce. So the
        # SLA sweep now only CLEANS UP cards whose candidate is no longer open
        # (rejected / hired / withdrawn); an open candidate's card persists as a
        # pending HITL recommendation until it's actioned or auto-corrected.
        stale = (
            db.query(AgentDecision)
            .join(
                CandidateApplication,
                CandidateApplication.id == AgentDecision.application_id,
            )
            .filter(
                AgentDecision.status == "pending",
                AgentDecision.decision_type != "escalate_low_confidence",
                AgentDecision.created_at < expiry_cutoff,
                not_snoozed,
                CandidateApplication.application_outcome != "open",
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
