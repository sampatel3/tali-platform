"""Candidate-facing transparency explanation.

Stub. When active, generates an explanation artifact the candidate sees
("Here's what the system looked at when reviewing your application,
and what decision was made"). When inactive, returns None — the
existing email/notification path is unchanged.

Risk: medium — requires `legal_communications` sign-off before live.
The flag substrate enforces "off by default"; the legal review is what
the human review_required gate tracks.
"""

from __future__ import annotations

from dataclasses import dataclass

from .._stub_helpers import CapabilityContext


CAPABILITY = "candidate_experience"


@dataclass
class CandidateExplanation:
    headline: str
    body: str
    citations: list[str]


def render_explanation(ctx: CapabilityContext) -> CandidateExplanation | None:
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: real explanation generation


__all__ = ["CAPABILITY", "CandidateExplanation", "render_explanation"]
