"""Canonical workspace and per-role autonomy policy helpers.

Workspace settings are copied onto a role when it is created.  They are not a
live pointer: changing workspace defaults never silently changes an existing
role's hiring workflow.  Nullable granular role fields preserve compatibility
with the historical ``auto_promote`` switch while allowing each reversible
action to be controlled independently.
"""

from __future__ import annotations

from typing import Any

from ..models.organization import Organization
from ..models.role import Role


GRANULAR_AUTOMATION_FIELDS = (
    "auto_send_assessment",
    "auto_resend_assessment",
    "auto_advance",
)

# Concrete defaults for a workspace that has never saved an ``agent_defaults``
# block. Keeping these here makes role creation, API serialization, activation,
# and every ATS constructor agree on the initial policy. They grant only
# reversible positive automation; deterministic rejection remains opt-in.
PLATFORM_AGENT_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "auto_send_assessment": True,
    "auto_resend_assessment": True,
    "auto_advance": True,
    "auto_reject_pre_screen": False,
    "auto_skip_assessment": False,
    "threshold_mode": None,
    "budget_cents": None,
    "agent_action_allowlist": None,
    "agent_token_budget_per_cycle": None,
    "agent_decision_budget_per_cycle": None,
}

FIXED_HUMAN_REVIEW_ACTIONS = (
    "full_score_reject",
    "assessment_reject",
    "llm_reject",
    "interview",
    "offer",
    "hire",
)


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def workspace_agent_defaults(org: Organization | None) -> dict[str, Any]:
    """Return the explicitly configured defaults in their canonical shape.

    The older Settings payload nested the three autonomy choices under
    ``autonomy``. Translate it here so saved pre-upgrade settings remain
    effective. An organization with no block receives the concrete safe
    platform defaults above.
    """
    defaults = dict(PLATFORM_AGENT_DEFAULTS)
    if org is None:
        return defaults
    ai_config = _dict(getattr(org, "ai_tooling_config", None))
    raw = ai_config.get("agent_defaults")
    if not isinstance(raw, dict):
        return defaults
    autonomy = _dict(raw.get("autonomy"))

    def canonical_bool(name: str, legacy_name: str | None = None) -> bool:
        if raw.get(name) is not None:
            return bool(raw.get(name))
        if legacy_name and legacy_name in autonomy:
            return bool(autonomy.get(legacy_name))
        return bool(defaults[name])

    defaults.update({
        "enabled": bool(raw.get("enabled", True)),
        "auto_send_assessment": canonical_bool(
            "auto_send_assessment", "auto_invite_above"
        ),
        "auto_resend_assessment": canonical_bool(
            "auto_resend_assessment", "auto_invite_above"
        ),
        "auto_advance": canonical_bool(
            "auto_advance", "auto_advance_high_score"
        ),
        "auto_reject_pre_screen": canonical_bool(
            "auto_reject_pre_screen", "auto_reject_below"
        ),
        "auto_skip_assessment": canonical_bool("auto_skip_assessment"),
        "threshold_mode": raw.get("threshold_mode"),
        "budget_cents": raw.get("budget_cents"),
        "agent_action_allowlist": raw.get("agent_action_allowlist"),
        "agent_token_budget_per_cycle": raw.get("agent_token_budget_per_cycle"),
        "agent_decision_budget_per_cycle": raw.get(
            "agent_decision_budget_per_cycle"
        ),
    })
    return defaults


def apply_workspace_agent_defaults(
    role: Role,
    org: Organization | None,
    *,
    explicit_budget_cents: int | None = None,
    explicit_score_threshold: int | None = None,
) -> Role:
    """Seed a newly-created role with effective workspace agent defaults."""
    defaults = workspace_agent_defaults(org)

    budget = explicit_budget_cents
    if budget is None and org is not None:
        budget = getattr(org, "default_role_budget_cents", None)
    if budget is None:
        budget = defaults.get("budget_cents")
    if budget is not None:
        role.monthly_usd_budget_cents = max(0, int(budget))

    threshold = explicit_score_threshold
    if threshold is None and org is not None:
        threshold = getattr(org, "default_score_threshold", None)
    if threshold is not None:
        role.score_threshold = max(0, min(100, int(threshold)))

    configured_mode = defaults.get("threshold_mode")
    if explicit_score_threshold is not None:
        # A value supplied on this role must be the value runtime actually uses.
        role.auto_reject_threshold_mode = "manual"
    elif configured_mode in {"manual", "auto"}:
        role.auto_reject_threshold_mode = str(configured_mode)
    elif threshold is not None:
        # Historical workspace threshold settings did not carry a mode.  Treat
        # those as manual so the saved number is not silently ignored by the
        # computed-auto path.
        role.auto_reject_threshold_mode = "manual"

    for field in GRANULAR_AUTOMATION_FIELDS:
        setattr(role, field, bool(defaults[field]))
    role.auto_reject_pre_screen = bool(defaults["auto_reject_pre_screen"])
    role.auto_skip_assessment = bool(defaults["auto_skip_assessment"])
    if defaults.get("agent_action_allowlist") is not None:
        role.agent_action_allowlist = list(defaults["agent_action_allowlist"])
    for field in (
        "agent_token_budget_per_cycle",
        "agent_decision_budget_per_cycle",
    ):
        value = defaults.get(field)
        if value is not None:
            setattr(role, field, int(value))

    granular = [getattr(role, field, None) for field in GRANULAR_AUTOMATION_FIELDS]
    # ``auto_promote`` is a compatibility aggregate only. A mixed policy is
    # represented as False while the concrete fields remain authoritative.
    role.auto_promote = all(bool(value) for value in granular)
    return role


def role_automation_enabled(role: Role, field: str) -> bool:
    """Resolve one granular action, falling back to legacy ``auto_promote``."""
    value = getattr(role, field, None)
    if value is None:
        return bool(getattr(role, "auto_promote", False))
    return bool(value)


def activation_policy_values(
    role: Role, updates: dict[str, Any] | None = None
) -> dict[str, bool]:
    """Resolve the policy snapshot a Turn-on command should persist.

    Concrete granular choices always win over the old aggregate switch. If a
    legacy role has no concrete choices at all, first activation retains the
    one-switch historical default (all reversible actions on).
    """
    updates = updates or {}
    concrete_before = any(
        getattr(role, field, None) is not None for field in GRANULAR_AUTOMATION_FIELDS
    )
    concrete_incoming = any(
        field in updates and updates.get(field) is not None
        for field in GRANULAR_AUTOMATION_FIELDS
    )
    legacy_incoming = updates.get("auto_promote")
    if not concrete_before and not concrete_incoming:
        legacy_default = (
            bool(legacy_incoming) if legacy_incoming is not None else True
        )
    else:
        legacy_default = bool(getattr(role, "auto_promote", False))

    resolved: dict[str, bool] = {}
    for field in GRANULAR_AUTOMATION_FIELDS:
        if field in updates and updates.get(field) is not None:
            value = bool(updates[field])
        elif getattr(role, field, None) is not None:
            value = bool(getattr(role, field))
        else:
            value = legacy_default
        resolved[field] = value
    resolved["auto_promote"] = all(resolved[field] for field in GRANULAR_AUTOMATION_FIELDS)
    return resolved


def automation_enabled_for_decision(role: Role, decision_type: str) -> bool:
    field_by_decision = {
        "advance_to_interview": "auto_advance",
        "send_assessment": "auto_send_assessment",
        "resend_assessment_invite": "auto_resend_assessment",
        "reject": "auto_reject",
        "skip_assessment_reject": "auto_reject",
    }
    field = field_by_decision.get(str(decision_type))
    if field is None:
        return False
    if field == "auto_reject":
        return bool(getattr(role, field, False))
    return role_automation_enabled(role, field)


def effective_agent_policy(role: Role) -> dict[str, Any]:
    """Stable, flat API representation of what runtime will enforce."""
    return {
        "version": 2,
        "auto_send_assessment": role_automation_enabled(
            role, "auto_send_assessment"
        ),
        "auto_resend_assessment": role_automation_enabled(
            role, "auto_resend_assessment"
        ),
        "auto_advance": role_automation_enabled(role, "auto_advance"),
        "auto_reject_pre_screen": bool(
            getattr(role, "auto_reject", False)
            or getattr(role, "auto_reject_pre_screen", False)
        ),
        "auto_skip_assessment": bool(
            getattr(role, "auto_skip_assessment", False)
        ),
        "threshold_mode": (
            getattr(role, "auto_reject_threshold_mode", None) or "auto"
        ),
        "score_threshold": getattr(role, "score_threshold", None),
        "monthly_budget_cents": getattr(role, "monthly_usd_budget_cents", None),
        "action_allowlist": getattr(role, "agent_action_allowlist", None),
        "action_allowlist_source": (
            "role" if getattr(role, "agent_action_allowlist", None) is not None
            else "safe_platform_default"
        ),
        "token_budget_per_cycle": getattr(
            role, "agent_token_budget_per_cycle", None
        ),
        "decision_budget_per_cycle": getattr(
            role, "agent_decision_budget_per_cycle", None
        ),
        "fixed_human_review": list(FIXED_HUMAN_REVIEW_ACTIONS),
        "metering": {
            # The hard credit ledger covers model and embedding calls. Other
            # operational providers (email, sandbox, storage, ATS) are tracked
            # as estimates separately and must not be represented as debited
            # AI usage in this policy contract.
            "llm_and_embedding_usage_metered": True,
            "operational_provider_costs_estimated_separately": True,
            "monthly_budget_enforced": getattr(
                role, "monthly_usd_budget_cents", None
            )
            is not None,
            "high_risk_actions_per_cycle": 1,
            "reject_recommendations_per_cycle": 5,
        },
    }
