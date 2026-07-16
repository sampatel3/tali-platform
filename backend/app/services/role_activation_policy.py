"""Policy snapshots carried by durable role-activation intents."""

from __future__ import annotations

from typing import Any

from ..models.role import Role
from .agent_policy_settings import activation_policy_values


def role_policy_snapshot(role: Role) -> dict[str, Any]:
    """Capture the current role policy an activation worker must preserve."""

    positive = activation_policy_values(role)
    allowlist = getattr(role, "agent_action_allowlist", None)
    return {
        "monthly_usd_budget_cents": getattr(role, "monthly_usd_budget_cents", None),
        "auto_promote": positive["auto_promote"],
        "auto_send_assessment": positive["auto_send_assessment"],
        "auto_resend_assessment": positive["auto_resend_assessment"],
        "auto_advance": positive["auto_advance"],
        "auto_reject": bool(getattr(role, "auto_reject", False)),
        "auto_reject_pre_screen": bool(
            getattr(role, "auto_reject_pre_screen", False)
        ),
        "auto_skip_assessment": bool(getattr(role, "auto_skip_assessment", False)),
        "auto_reject_threshold_mode": getattr(
            role,
            "auto_reject_threshold_mode",
            None,
        ),
        "score_threshold": getattr(role, "score_threshold", None),
        "agent_action_allowlist": (
            list(allowlist) if isinstance(allowlist, (list, tuple)) else None
        ),
        "agent_token_budget_per_cycle": getattr(
            role,
            "agent_token_budget_per_cycle",
            None,
        ),
        "agent_decision_budget_per_cycle": getattr(
            role,
            "agent_decision_budget_per_cycle",
            None,
        ),
    }


__all__ = ["role_policy_snapshot"]
