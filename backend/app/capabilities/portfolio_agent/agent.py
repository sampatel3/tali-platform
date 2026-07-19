"""Fail-closed compatibility surface for portfolio feature computation."""

from __future__ import annotations

from .._stub_helpers import CapabilityContext, raise_unavailable


CAPABILITY = "portfolio_agent"


def contribute(ctx: CapabilityContext) -> dict[str, float]:
    """Reject use until the registry declares a complete implementation."""

    del ctx
    raise_unavailable(CAPABILITY)


__all__ = ["CAPABILITY", "contribute"]
