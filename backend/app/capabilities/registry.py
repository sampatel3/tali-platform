"""Static capability registry — Section 4 of capability_flags_addendum.md.

Adding a new capability is a code change with a PR review; no dynamic
loading. The registry tells every consumer:
  - what each capability does
  - which v1/v2 modules it extends or replaces
  - what other capabilities it requires
  - whether it can be disabled mid-cycle (rollback_safe)
  - what sign-offs are needed before enabling

The v1/v2 surfaces this is built on top of:
  - ``orchestrator``         the existing run_cycle (app.agent_runtime.orchestrator)
  - ``policy_engine``        decision_policy.engine.evaluate
  - ``policy_fitter``        decision_policy.nightly_policy_fit
  - ``promotion_gate``       decision_policy.promotion_gate
  - ``pre_screen`` / ``cv_scoring`` / ``assessment_scoring`` / ``graph_priors``
  - ``shared_action_layer``  app.actions.*
  - ``recruiter_review``     domains.agentic.* (approve / override / teach)
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


CAPABILITIES: dict[str, Capability] = {
    "portfolio_agent": Capability(
        name="portfolio_agent",
        description="Cohort-level reasoning: team shape, balance, pipeline composition.",
        extends=("policy_engine",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "intervention_agent": Capability(
        name="intervention_agent",
        description="Proposes role-spec tweaks and outreach actions based on pipeline patterns.",
        extends=("recruiter_review",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "candidate_experience": Capability(
        name="candidate_experience",
        description="Generates candidate-facing explanations and transparency artifacts.",
        extends=("shared_action_layer",),
        replaces=(),
        requires=(),
        risk="medium",
        review_required=("legal_communications",),
        rollback_safe=True,
    ),
    "reasoning_orchestrator": Capability(
        name="reasoning_orchestrator",
        description="LLM-driven orchestration: routes by uncertainty, plans workflow, admits OOD.",
        extends=(),
        replaces=("orchestrator",),
        requires=("drift_monitor",),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
    "bidirectional_subagents": Capability(
        name="bidirectional_subagents",
        description="Sub-agents request artifacts, propose counterfactuals, explain themselves.",
        extends=("pre_screen", "cv_scoring", "assessment_scoring", "graph_priors"),
        replaces=(),
        requires=("reasoning_orchestrator",),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
    "causal_policy": Capability(
        name="causal_policy",
        description="Causal policy engine: tracks causal claims, validates downstream.",
        extends=("policy_engine",),
        replaces=(),
        requires=(),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
    "online_learning": Capability(
        name="online_learning",
        description="Policy updates within minutes of outcomes/overrides, within safety bounds.",
        extends=("policy_fitter",),
        replaces=(),
        requires=("causal_policy", "drift_monitor", "bias_monitor_continuous"),
        risk="high",
        review_required=("compliance",),
        rollback_safe=True,
    ),
    "federated_graph": Capability(
        name="federated_graph",
        description="Anonymized cross-org signal exchange under DP and contractual bounds.",
        extends=("graph_priors",),
        replaces=(),
        requires=(),
        risk="high",
        review_required=("legal", "privacy_dpo", "infosec"),
        rollback_safe=True,
    ),
    "drift_monitor": Capability(
        name="drift_monitor",
        description="Detects distribution shift and OOD candidates/roles.",
        extends=(),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "bias_monitor_continuous": Capability(
        name="bias_monitor_continuous",
        description="Continuous fairness audit — v1's gated audit becomes a meta-agent.",
        extends=("promotion_gate",),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "causal_validator": Capability(
        name="causal_validator",
        description="Tests whether the system's causal claims hold up against realized outcomes.",
        extends=(),
        replaces=(),
        requires=("causal_policy",),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
    "capability_auditor": Capability(
        name="capability_auditor",
        description="Adversarial meta-agent identifying what the system is bad at.",
        extends=(),
        replaces=(),
        requires=(),
        risk="medium",
        review_required=(),
        rollback_safe=True,
    ),
    "hiring_manager_dialog": Capability(
        name="hiring_manager_dialog",
        description="Interactive role-spec shaping with the hiring manager.",
        extends=(),
        replaces=(),
        requires=(),
        risk="low",
        review_required=(),
        rollback_safe=True,
    ),
}


ALL_CAPABILITIES: tuple[str, ...] = tuple(CAPABILITIES.keys())


def get(name: str) -> Capability | None:
    return CAPABILITIES.get(name)


__all__ = ["ALL_CAPABILITIES", "CAPABILITIES", "Capability", "get"]
