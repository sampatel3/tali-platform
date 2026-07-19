"""Read-only Bullhorn connection-health projections for API routes."""

from __future__ import annotations

from ...components.integrations.bullhorn.event_state import POISON_STATE_KEY, poll_intent
from ...components.integrations.bullhorn.event_subscriptions import (
    SubscriptionProvenanceError,
    validate_subscription_provenance,
)
from ...models.organization import Organization


def event_subscription_health(org: Organization) -> tuple[bool, str]:
    """Return local lifecycle health without probing or mutating Bullhorn."""
    if not org.bullhorn_connected:
        return False, "disconnected"
    if not str(org.bullhorn_event_subscription_id or "").strip():
        return False, "missing"
    try:
        lifecycle = validate_subscription_provenance(org)
    except SubscriptionProvenanceError:
        return False, "invalid_provenance"
    if lifecycle.get("state") != "active":
        return False, "pending"
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    if isinstance(config.get(POISON_STATE_KEY), dict):
        return False, "poison_retry_pending"
    if str(org.bullhorn_event_request_id or "").strip() or poll_intent(org):
        return False, "checkpoint_pending"
    return True, "active"
