"""Celery tasks for the autonomous recruiting agent.

The actual cycle work lives in ``app.agent_runtime.orchestrator``;
these are thin wrappers that own the DB session lifecycle and Celery
plumbing.

Triggers:
- ``agent_react_to_event``: enqueued from ``app.services.application_events``
  when a relevant event happens on a role with ``agentic_mode_enabled=true``.
- ``agent_manual_run``: invoked from the manual-trigger API endpoint and
  the ``scripts/run_agent_cycle.py`` CLI.
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
