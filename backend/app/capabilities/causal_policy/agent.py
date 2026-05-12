"""Causal policy contribution — extends `policy_engine`.

Tracks causal claims (which feature caused which outcome) and lets the
``causal_validator`` meta-agent test those claims against realised
outcomes downstream. When inactive, returns None and the policy engine
uses its correlational path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "causal_policy"


@dataclass
class CausalClaim:
    feature: str
    effect_on_outcome: float
    confidence: float
    derivation: str = ""


@dataclass
class CausalDecisionContribution:
    recommended_action: str | None = None
    confidence: float = 0.0
    claims: list[CausalClaim] = field(default_factory=list)


def decide_causal(
    ctx: CapabilityContext, *, features: dict[str, float]
) -> CausalDecisionContribution | None:
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: causal inference layer


__all__ = ["CAPABILITY", "CausalClaim", "CausalDecisionContribution", "decide_causal"]
