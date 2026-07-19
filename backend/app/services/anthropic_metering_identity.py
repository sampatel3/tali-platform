"""Exact scalar attribution at Anthropic provider boundaries."""

from __future__ import annotations

from typing import Any


def resolve_organization_id(
    *,
    client_organization_id: Any,
    metering: dict[str, Any],
    require_client_match: bool,
) -> int | None:
    """Resolve an exact positive int without bool/string coercion."""

    bound = client_organization_id
    override_supplied = "organization_id" in metering
    override = metering.get("organization_id")
    if bound is not None and (type(bound) is not int or bound <= 0):
        raise ValueError("client organization_id must be a positive integer")
    if override_supplied and (type(override) is not int or override <= 0):
        raise ValueError("organization_id must be a positive integer")
    if require_client_match and bound is not None and override_supplied and override != bound:
        raise ValueError("organization_id does not match the client organization")
    return override if override_supplied else bound


def require_role_and_metadata_types(
    metering: dict[str, Any],
) -> tuple[int | None, int | None, str | None, int | None, dict[str, Any] | None]:
    """Return exact optional role/metadata values or reject coercible lookalikes."""

    role_id = metering.get("role_id")
    if role_id is not None and (type(role_id) is not int or role_id <= 0):
        raise ValueError("role_id must be a positive integer")
    user_id = metering.get("user_id")
    if user_id is not None and (type(user_id) is not int or user_id <= 0):
        raise ValueError("user_id must be a positive integer")
    entity_id = metering.get("entity_id")
    if entity_id is not None and (
        type(entity_id) is not str or not entity_id.strip()
    ):
        raise ValueError("entity_id must be a non-empty string")
    candidate_id = metering.get("candidate_id")
    if candidate_id is not None and (
        type(candidate_id) is not int or candidate_id <= 0
    ):
        raise ValueError("candidate_id must be a positive integer")
    metadata = metering.get("metadata")
    if metadata is not None and type(metadata) is not dict:
        raise ValueError("metering metadata must be an object")
    return role_id, user_id, entity_id, candidate_id, metadata


__all__ = ["require_role_and_metadata_types", "resolve_organization_id"]
