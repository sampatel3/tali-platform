"""Continuous bias monitor — extends the v1 promotion-gate audit.

v1/v2 runs the bias audit once, at promotion time. This capability
turns it into a meta-agent that watches every decision against the
realised-outcome stream, flagging emerging disparate impact in real
time. Required by ``online_learning`` as one of its safety guardrails.

When inactive, the v1/v2 promotion-gate audit still runs — this
capability adds a *second* surface that operates continuously, not a
replacement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "bias_monitor_continuous"


@dataclass
class StreamingAuditReport:
    new_violations: list[dict] = field(default_factory=list)
    notes: str = ""


def audit_streaming(ctx: CapabilityContext) -> StreamingAuditReport:
    if not ctx.is_active(CAPABILITY):
        return StreamingAuditReport()
    return StreamingAuditReport()  # TODO: streaming audit


__all__ = ["CAPABILITY", "StreamingAuditReport", "audit_streaming"]
