"""Capability registry — single source of truth per §12 of
``recruitment_system_architecture.md``.

The spec is explicit: v10 reserves **four** capabilities on the v1 spine —
portfolio_agent, capability_auditor, bias_monitor_continuous, and
causal_mode.

Two earlier scaffolds the spec **argues against** (LLM-driven
orchestrator, federated cross-org graph) are NOT included. Eight more
capabilities from the earlier capability-flags addendum that don't
appear in the final spec have been deleted along with their folders
in the same change-set — there is one version of this system; the
registry is the source of truth.

Adding a new capability is a code change with PR review; the static dict here
is loaded once at import time by the flag client. Registry entries describe
rollout contracts, not implementations. An entry stays unavailable until a
production caller is wired and tested. Historical package paths expose only
explicit fail-closed compatibility APIs so imports remain stable without
making an unimplemented feature look active.
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
    available: bool
    unavailable_reason: str | None = None


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
        available=False,
        unavailable_reason="Scaffold only; cohort feature computation is not implemented.",
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
        available=False,
        unavailable_reason="Scaffold only; the capability audit does not produce findings yet.",
    ),
    "bias_monitor_continuous": Capability(
        name="bias_monitor_continuous",
        description=(
            "Rolling fairness audit over actual pre-screen, fraud-cap, and "
            "automated-reject outcomes. Reads the latest small-cell-suppressed "
            "aggregate generated from segregated voluntary EEO self-ID."
        ),
        extends=("promotion_gate",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
        available=False,
        unavailable_reason=(
            "The aggregate monitor is implemented behind "
            "PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED, but this per-org "
            "capability flag is not wired to scheduling or alert delivery."
        ),
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
        available=False,
        unavailable_reason="Scaffold only; causal inference and claim validation are not implemented.",
    ),
}


ALL_CAPABILITIES: tuple[str, ...] = tuple(CAPABILITIES.keys())


def get(name: str) -> Capability | None:
    return CAPABILITIES.get(name)


__all__ = ["ALL_CAPABILITIES", "CAPABILITIES", "Capability", "get"]
