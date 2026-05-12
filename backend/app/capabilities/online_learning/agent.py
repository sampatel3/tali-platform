"""Online policy updates — high risk, requires compliance sign-off.

Extends the nightly batch policy fitter with within-minutes updates
from realised outcomes + recruiter overrides. Requires
``causal_policy``, ``drift_monitor``, and ``bias_monitor_continuous``
— the three guardrails that make online updates safe.

When inactive (default), the nightly batch path remains the only writer
to ``policy_versions`` and the promotion gate still mediates promotion.
"""

from __future__ import annotations

from .._stub_helpers import CapabilityContext


CAPABILITY = "online_learning"


def maybe_update_policy(ctx: CapabilityContext, *, signal: dict) -> bool:
    """Apply an in-minutes policy update from a fresh signal.

    Returns True iff an update was applied. When the flag is off, no-op.
    Real implementation must respect the same promotion gate (gold eval
    + bias audit + shadow mode) the nightly path goes through —
    online_learning shortens the loop, never the safety bar.
    """
    if not ctx.is_active(CAPABILITY):
        return False
    return False  # TODO: gated online update


__all__ = ["CAPABILITY", "maybe_update_policy"]
