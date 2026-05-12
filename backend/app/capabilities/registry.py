"""Capability registry — single source of truth per §12 of
``recruitment_system_architecture.md``.

The spec is explicit: v10 adds **four** capabilities to the v1 spine —
portfolio_agent, capability_auditor, bias_monitor_continuous, and
causal_mode (a toggle on the policy model, not a separate folder).

Two earlier scaffolds the spec **argues against** (LLM-driven
orchestrator, federated cross-org graph) are NOT included. Eight more
capabilities from the earlier capability-flags addendum that don't
appear in the final spec have been deleted along with their folders
in the same change-set — there is one version of this system; the
registry is the source of truth.

Adding a new capability is a code change with PR review; the static
dict here is loaded once at import time by the flag client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    extends: tuple[str, ...]
    replaces: tuple[str, ...]
    requires: tuple[str, ...]
    risk: str  # "low" | "medium" | "high"
    review_required: tuple[str, ...]
    rollback_safe: bool


# §12: the canonical v10 capability set.
CAPABILITIES: dict[str, Capability] = {
    "portfolio_agent": Capability(
        name="portfolio_agent",
        description=(
            "Stage-4 cohort-level reasoning: team shape, balance, "
            "pipeline composition. Contributes a cohort adjustment to "
            "the policy-engine recommendation."
        ),
        extends=("policy_engine",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "capability_auditor": Capability(
        name="capability_auditor",
        description=(
            "Weekly meta-agent. Finds blind spots: role families where "
            "calibration is poor, profiles where overrides cluster, "
            "task templates whose predictive quality has decayed. "
            "Reads from Graphiti, outputs structured reports."
        ),
        extends=(),
        replaces=(),
        requires=(),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
    "bias_monitor_continuous": Capability(
        name="bias_monitor_continuous",
        description=(
            "Continuous fairness audit. v1's bias audit fires only at "
            "promotion time; this runs on every decision. Same shape "
            "as the capability auditor — outside the spine, reading "
            "from Graphiti."
        ),
        extends=("promotion_gate",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "causal_mode": Capability(
        name="causal_mode",
        description=(
            "Toggle on the policy model: track 'we advanced X because "
            "of Y' as a structured causal claim and validate against "
            "downstream outcomes. Same stage; different math. Not a "
            "separate component — the flag flips a mode on the fitted "
            "policy."
        ),
        extends=("policy_engine",),
        replaces=(),
        requires=(),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
}


ALL_CAPABILITIES: tuple[str, ...] = tuple(CAPABILITIES.keys())


def get(name: str) -> Capability | None:
    return CAPABILITIES.get(name)


__all__ = ["ALL_CAPABILITIES", "CAPABILITIES", "Capability", "get"]
