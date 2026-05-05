"""Centralised pre-spend gate for role-level monthly USD cap.

When a role has a monthly cap set (typically because agentic mode was
activated and the recruiter chose a budget), every Anthropic-spending
entry point with role context — scoring, pre-screen, assessment, and the
agent itself — should call ``can_spend_on_role`` first and skip cleanly
when the cap is exhausted. This is the universal budget rule: when you
turn agentic mode on, the cap covers *everything* the platform spends on
that role for the month.

The implementation defers to ``agent_runtime.budget_guard`` so there's
exactly one place that defines what "over budget" means.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..models.role import Role
from ..agent_runtime.budget_guard import check_monthly_usd, pause_role

logger = logging.getLogger("taali.role_budget_gate")


def can_spend_on_role(db: Session, *, role: Optional[Role]) -> bool:
    """Return True if a fresh Anthropic call against ``role`` is allowed.

    No-ops to True when:
    - ``role`` is None (legacy callsite without role context)
    - ``role.monthly_usd_budget_cents`` is unset

    Auto-pauses the role on first cap-breach so the agent loop also
    notices on its next tick.
    """
    if role is None:
        return True
    if role.monthly_usd_budget_cents is None or int(role.monthly_usd_budget_cents) <= 0:
        return True
    check = check_monthly_usd(db, role=role)
    if check.ok:
        return True
    if role.agent_paused_at is None:
        try:
            pause_role(db, role=role, reason=check.reason or "monthly USD cap reached")
            db.flush()
        except Exception:
            logger.exception("auto-pause failed for role_id=%s", role.id)
    logger.info(
        "role_budget_gate: role_id=%s blocked (%s)",
        role.id,
        check.reason,
    )
    return False
