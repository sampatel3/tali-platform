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
