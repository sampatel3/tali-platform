"""v10 capability flag substrate + scaffolded capability folders.

Public API:

    from app.capabilities import get_shared, ALL_CAPABILITIES, CAPABILITIES

    snapshot = get_shared().snapshot(
        ALL_CAPABILITIES,
        db=db,
        organization_id=org_id,
        decision_id=f"{run_id}:{app_id}:{decision_type}",
        role_id=role_id,
        role_family=role_family,
    )
    # ... pass `snapshot` through the decision pipeline; it is persisted
    # on AgentDecision.active_capabilities (see queue_decision.run).

Each capability folder under this package is a self-contained slice:
its own stub, prompts, tests, README. Disabling a capability is a flag
change — the folder stays in place. See ``capability_flags_addendum.md``
§6 for the layout convention.
"""

from .flags import CapabilityFlags, FlagScope, get_shared
from .registry import ALL_CAPABILITIES, CAPABILITIES, Capability


__all__ = [
    "ALL_CAPABILITIES",
    "CAPABILITIES",
    "Capability",
    "CapabilityFlags",
    "FlagScope",
    "get_shared",
]
