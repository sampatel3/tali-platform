"""Capability auditor — adversarial meta-agent.

Asks: what is this system bad at? Mines override patterns, recurring
teach signals, escalations, and outcomes to surface the categories
of cases the system handles worst. Returns a ranked list of weak
spots.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "capability_auditor"


@dataclass
class WeakSpot:
    category: str
    severity: float
    evidence: list[str] = field(default_factory=list)


def audit_capabilities(ctx: CapabilityContext) -> list[WeakSpot]:
    if not ctx.is_active(CAPABILITY):
        return []
    return []  # TODO: adversarial probe


__all__ = ["CAPABILITY", "WeakSpot", "audit_capabilities"]
