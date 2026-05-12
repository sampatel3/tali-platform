"""Causal-mode toggle on the policy model (§12).

The spec calls this a "mode toggle on the policy model rather than a
new component." Same stage in the pipeline (stage 4 — fitted policy),
different math: tracks 'we advanced X because of Y' as a structured
causal claim and validates against downstream outcomes.

When the flag is on, the policy engine consults this module to attach
causal claims to its verdict. When off, the engine runs the
correlational path it does today. This is a thin scaffold — the real
causal-inference math lands when the policy engine has a
``causal_claims`` evidence channel to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "causal_mode"


@dataclass
class CausalClaim:
    feature: str
    effect_on_outcome: float
    confidence: float
    derivation: str = ""


@dataclass
class CausalModeContribution:
    claims: list[CausalClaim] = field(default_factory=list)
    note: str = ""


def decide_causal_mode(
    ctx: CapabilityContext, *, features: dict[str, float]
) -> CausalModeContribution | None:
    """Return causal claims for the decision when the toggle is on.

    Returns None when the toggle is off — caller falls through to the
    existing correlational policy path with no change in behaviour.
    """
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: causal inference layer (spec v10 §12)


__all__ = ["CAPABILITY", "CausalClaim", "CausalModeContribution", "decide_causal_mode"]
