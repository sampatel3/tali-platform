"""Intervention capability — proposes role-spec tweaks + outreach actions.

Stub. When active, returns a list of structured proposals the recruiter
review surface can render alongside the queued decision. When inactive,
returns an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "intervention_agent"


@dataclass
class InterventionProposal:
    kind: str  # "spec_tweak" | "outreach" | "redirect"
    summary: str
    rationale: str = ""
    structured_payload: dict = field(default_factory=dict)


def propose(ctx: CapabilityContext) -> list[InterventionProposal]:
    if not ctx.is_active(CAPABILITY):
        return []
    # TODO: real implementation reads pipeline patterns (rejection
    # reasons, narrow-skill gaps, latency to first response) and emits
    # concrete proposals.
    return []


__all__ = ["CAPABILITY", "InterventionProposal", "propose"]
