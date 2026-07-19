"""v10 capability flag substrate and compatibility package exports.

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

The four reserved capability package paths remain importable for downstream
compatibility, but their call surfaces fail closed while the registry marks
them unavailable. They are not production implementations and are not wired
into the decision pipeline.
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
