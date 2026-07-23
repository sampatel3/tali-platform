"""Durable provenance for recruiter resolutions of agent decisions.

The queue payload is delivery machinery, not an audit log.  Anything needed to
answer a later question about what the recruiter requested is stored on the
``AgentDecision`` before the background operation is published.
"""

from __future__ import annotations

from typing import Any

from ..models.agent_decision import AgentDecision


def record_resolution_request(
    decision: AgentDecision,
    *,
    requested_action: str | None,
    target_stage: str | None = None,
) -> dict[str, Any]:
    """Merge the immutable request facts into ``resolution_metadata``."""

    metadata = dict(decision.resolution_metadata or {})
    metadata.update(
        {
            "requested_action": (str(requested_action or "").strip() or None),
            "target_stage": (str(target_stage or "").strip() or None),
            "acting_role_id": int(decision.role_id),
            "application_id": int(decision.application_id),
        }
    )
    decision.resolution_metadata = metadata
    return metadata


def requested_target_stage(
    decision: AgentDecision,
    explicit_target_stage: str | None = None,
) -> str | None:
    """Return the durable target, with payload fallback for legacy jobs."""

    metadata = decision.resolution_metadata or {}
    stored = metadata.get("target_stage") if isinstance(metadata, dict) else None
    durable = str(stored or "").strip()
    if durable:
        return durable
    return str(explicit_target_stage or "").strip() or None


def record_resolution_effect(
    decision: AgentDecision,
    *,
    effect_status: str,
    provider_movement_confirmed: bool | None = None,
) -> None:
    """Record the observed effect without replacing the original request."""

    metadata = dict(decision.resolution_metadata or {})
    metadata["effect_status"] = str(effect_status or "").strip().lower()
    if provider_movement_confirmed is not None:
        metadata["provider_movement_confirmed"] = bool(provider_movement_confirmed)
    decision.resolution_metadata = metadata


__all__ = [
    "record_resolution_effect",
    "record_resolution_request",
    "requested_target_stage",
]
