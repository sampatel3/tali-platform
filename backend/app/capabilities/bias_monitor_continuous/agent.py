"""Fail-closed compatibility surface for the continuous bias monitor."""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext, raise_unavailable


CAPABILITY = "bias_monitor_continuous"


@dataclass
class StreamingAuditReport:
    """Historical response shape; no report is fabricated while unavailable."""

    new_violations: list[dict] = field(default_factory=list)
    notes: str = ""


def audit_streaming(ctx: CapabilityContext) -> StreamingAuditReport:
    """Reject use until the registry declares a complete implementation."""

    del ctx
    raise_unavailable(CAPABILITY)


__all__ = ["CAPABILITY", "StreamingAuditReport", "audit_streaming"]
