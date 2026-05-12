"""Portfolio capability — cohort-level scoring contribution.

Adds team-shape / pipeline-balance features to the policy engine when
``portfolio_agent`` is active. When the flag is off, ``contribute``
returns an empty dict — the v1 policy path is unchanged.

Implementation: stub. The real implementation reads the role's
current accepted pipeline + the active candidate set, computes shape
features (seniority distribution, skill coverage, time-zone spread),
and returns them keyed by feature name for the policy engine to
consume alongside the existing sub-agent scores.
"""

from __future__ import annotations

from .._stub_helpers import CapabilityContext


CAPABILITY = "portfolio_agent"


def contribute(ctx: CapabilityContext) -> dict[str, float]:
    """Return a dict of additional policy features for this decision.

    Empty when the flag is off. Caller (policy engine) merges into its
    feature vector without branching on the capability name.
    """
    if not ctx.is_active(CAPABILITY):
        return {}
    # TODO: real implementation. Stub returns placeholder zero features
    # so flag-on/flag-off don't change observable behaviour until the
    # real cohort signals land.
    return {
        "portfolio_team_shape_balance": 0.0,
        "portfolio_pipeline_diversity": 0.0,
    }


__all__ = ["CAPABILITY", "contribute"]
