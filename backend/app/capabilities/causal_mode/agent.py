"""Fail-closed compatibility surface for causal policy mode."""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext, raise_unavailable


CAPABILITY = "causal_mode"


@dataclass
class CausalClaim:
    """Historical response shape retained for downstream type imports."""

    feature: str
    effect_on_outcome: float
    confidence: float
    derivation: str = ""


@dataclass
class CausalModeContribution:
    """Historical response shape; never constructed by the unavailable API."""

    claims: list[CausalClaim] = field(default_factory=list)
    note: str = ""


def decide_causal_mode(
    ctx: CapabilityContext, *, features: dict[str, float]
) -> CausalModeContribution | None:
    """Reject use until the registry declares a complete implementation."""

    del ctx, features
    raise_unavailable(CAPABILITY)


__all__ = [
    "CAPABILITY",
    "CausalClaim",
    "CausalModeContribution",
    "decide_causal_mode",
]
