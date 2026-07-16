"""Fail-closed compatibility surface for the capability auditor."""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext, raise_unavailable


CAPABILITY = "capability_auditor"


@dataclass
class WeakSpot:
    """Historical response shape retained for downstream type imports."""

    category: str
    severity: float
    evidence: list[str] = field(default_factory=list)


def audit_capabilities(ctx: CapabilityContext) -> list[WeakSpot]:
    """Reject use until the registry declares a complete implementation."""

    del ctx
    raise_unavailable(CAPABILITY)


__all__ = ["CAPABILITY", "WeakSpot", "audit_capabilities"]
