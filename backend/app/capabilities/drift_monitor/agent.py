"""Drift / OOD monitor — meta-agent.

Detects distribution shift in incoming candidates/roles + out-of-
distribution cases. When inactive, returns an empty report; when
active, flags the decision-time inputs for the reasoning_orchestrator
to route differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "drift_monitor"


@dataclass
class DriftReport:
    distribution_shift_detected: bool = False
    ood_candidate: bool = False
    notes: list[str] = field(default_factory=list)


def check_drift(ctx: CapabilityContext, *, features: dict[str, float]) -> DriftReport:
    if not ctx.is_active(CAPABILITY):
        return DriftReport()
    return DriftReport()  # TODO: real distribution-shift detector


__all__ = ["CAPABILITY", "DriftReport", "check_drift"]
