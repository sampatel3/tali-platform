"""Agent-control tools — activate / pause the role's agent and adjust its
settings from the chat.

Mirrors the role-update PATCH in ``assessments_runtime/roles_management_routes.py``
(budget gate on activate, clear-pause on resume, auto-sync star, an immediate
cycle kick) and reuses the SAME helpers — ``budget_guard.resume_if_under_budget``
and the ``agent_daily_review_role`` task — so steering from chat and from the
settings UI stay in lockstep. Commits before kicking a cycle so the worker
sees the new state (same ordering the route uses).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role

logger = logging.getLogger("taali.agent_chat.controls")

_ACTIVATE = {"activate", "resume", "enable", "start", "restart", "on", "unpause"}
_PAUSE = {"pause", "stop", "hold", "suspend"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(role: Role) -> dict[str, Any]:
    return {
        "enabled": bool(role.agentic_mode_enabled),
        "paused": role.agent_paused_at is not None,
        "paused_reason": role.agent_paused_reason,
        "monthly_budget_cents": role.monthly_usd_budget_cents,
        "auto_reject": bool(role.auto_reject),
        "auto_promote": bool(role.auto_promote),
    }


def _kick_cycle(role: Role) -> None:
    """Enqueue an immediate daily-review cycle (same as the settings UI on
    activate/resume). Never block the chat turn on a broker hiccup."""
    try:
        from ..tasks.agent_tasks import agent_daily_review_role

        agent_daily_review_role.delay(int(role.id))
    except Exception:  # pragma: no cover — best-effort; the beat sweep catches up
        logger.exception("failed to enqueue agent cycle for role_id=%s", role.id)


def set_agent_state(db: Session, role: Role, *, action: str) -> dict[str, Any]:
    """``activate`` (turn on / resume) or ``pause`` the role's agent."""
    act = (action or "").strip().lower()

    if act in _ACTIVATE:
        # The agent can't run uncapped — activation needs a monthly budget
        # (mirrors the settings UI). Surface a clear ask instead of failing.
        if role.monthly_usd_budget_cents is None or int(role.monthly_usd_budget_cents) <= 0:
            return {
                "type": "agent_state", "ok": False, "reason": "needs_budget",
                "message": (
                    "I can't enable the agent without a monthly spend cap — set a "
                    "monthly budget for this role first (or tell me one to set)."
                ),
                "agent": _state(role),
            }
        was_enabled = bool(role.agentic_mode_enabled)
        was_paused = role.agent_paused_at is not None
        role.agentic_mode_enabled = True
        if role.agent_paused_at is not None:        # re-enabling clears the pause
            role.agent_paused_at = None
            role.agent_paused_reason = None
        if not role.starred_for_auto_sync:          # agent-on implies auto-sync
            role.starred_for_auto_sync = True
        db.commit()
        if (not was_enabled) or was_paused:         # activation OR resume → kick a cycle
            _kick_cycle(role)
        return {"type": "agent_state", "ok": True, "action": "activated", "agent": _state(role)}

    if act in _PAUSE:
        role.agent_paused_at = _now()
        role.agent_paused_reason = "paused by recruiter"
        db.commit()
        return {"type": "agent_state", "ok": True, "action": "paused", "agent": _state(role)}

    return {
        "type": "agent_state", "ok": False, "reason": "unknown_action",
        "message": f"I didn't recognise '{action}' — say 'activate' or 'pause'.",
        "agent": _state(role),
    }


def adjust_agent_settings(
    db: Session, role: Role, *,
    monthly_budget_cents: int | None = None,
    auto_reject: bool | None = None,
    auto_promote: bool | None = None,
) -> dict[str, Any]:
    """Update budget / auto-reject / auto-promote. Only the fields passed are
    changed. Raising the budget over month-to-date spend resumes a
    budget-paused role (same helper as the settings UI)."""
    changed: list[str] = []
    if monthly_budget_cents is not None:
        role.monthly_usd_budget_cents = max(0, int(monthly_budget_cents))
        changed.append("monthly_budget")
    if auto_reject is not None:
        role.auto_reject = bool(auto_reject)
        changed.append("auto_reject")
    if auto_promote is not None:
        role.auto_promote = bool(auto_promote)
        changed.append("auto_promote")

    resumed = False
    if monthly_budget_cents is not None:
        try:
            from ..agent_runtime import budget_guard

            resumed = bool(budget_guard.resume_if_under_budget(db, role=role))
        except Exception:  # pragma: no cover — never block the turn
            logger.exception("resume_if_under_budget failed for role_id=%s", role.id)

    db.commit()
    if resumed:
        _kick_cycle(role)
    return {
        "type": "agent_settings", "ok": True, "changed": changed,
        "resumed": resumed, "agent": _state(role),
    }


__all__ = ["set_agent_state", "adjust_agent_settings"]
