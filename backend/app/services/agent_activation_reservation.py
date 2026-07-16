"""Capability-accurate credit preflight for role-agent activation."""

from __future__ import annotations

from ..models.role import Role
from .agent_policy_settings import role_is_score_only
from .pricing_service import Feature, estimate_reservation


def activation_minimum_credits(role: Role, *, uses_assessment: bool) -> int:
    """Reserve one pass through only the capabilities this role dispatches."""

    score_only = role_is_score_only(role)
    features = (
        [Feature.SCORE]
        if score_only
        else [
            Feature.CV_PARSE,
            Feature.PRESCREEN,
            Feature.SCORE,
            Feature.AGENT_AUTONOMOUS,
        ]
    )
    if uses_assessment and not score_only:
        features.append(Feature.ASSESSMENT)
    return sum(estimate_reservation(feature) for feature in features)


__all__ = ["activation_minimum_credits"]
