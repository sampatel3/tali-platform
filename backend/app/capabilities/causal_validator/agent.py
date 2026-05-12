"""Causal validator — meta-agent.

Tests whether the causal claims recorded by ``causal_policy`` hold up
against realised hiring outcomes. Returns a list of confirmed /
weakened / falsified claims for the policy fitter to consult.

Requires ``causal_policy`` — there's nothing to validate without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "causal_validator"


@dataclass
class ValidationReport:
    confirmed: list[str] = field(default_factory=list)
    weakened: list[str] = field(default_factory=list)
    falsified: list[str] = field(default_factory=list)


def validate_claims(ctx: CapabilityContext) -> ValidationReport:
    if not ctx.is_active(CAPABILITY):
        return ValidationReport()
    return ValidationReport()  # TODO: claim validation


__all__ = ["CAPABILITY", "ValidationReport", "validate_claims"]
