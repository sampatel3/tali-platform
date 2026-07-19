"""Strict validation for Bullhorn event batches before durable advancement."""

from __future__ import annotations

from .event_handlers import SUBSCRIBED_ENTITIES, normalize_event_type
from .event_state import normalize_request_id


class InvalidEventBatch(RuntimeError):
    """A consumed or replayed provider batch is not safe to acknowledge."""


def validate_event(event: object) -> dict:
    if not isinstance(event, dict):
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    entity_name = event.get("entityName")
    entity_id = event.get("entityId")
    if not isinstance(entity_name, str) or entity_name not in SUBSCRIBED_ENTITIES:
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    if (
        isinstance(entity_id, bool)
        or not isinstance(entity_id, (str, int))
        or not str(entity_id).isascii()
        or not str(entity_id).isdigit()
        or not 1 <= len(str(entity_id)) <= 20
        or int(entity_id) <= 0
    ):
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    if normalize_event_type(event) is None:
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    updated = event.get("updatedProperties")
    if updated is not None and (
        not isinstance(updated, list)
        or any(not isinstance(field, str) or not field for field in updated)
    ):
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    return event


def validate_event_batch(
    payload: object,
    *,
    expected_request_id: object | None = None,
) -> tuple[str, list[dict]]:
    if not isinstance(payload, dict):
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    try:
        request_id = normalize_request_id(payload.get("requestId"))
        expected = (
            normalize_request_id(expected_request_id)
            if expected_request_id is not None
            else None
        )
    except RuntimeError:
        raise InvalidEventBatch("Bullhorn returned an invalid event batch") from None
    if expected is not None and request_id != expected:
        raise InvalidEventBatch("Bullhorn returned a conflicting event replay anchor")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise InvalidEventBatch("Bullhorn returned an invalid event batch")
    return request_id, [validate_event(event) for event in raw_events]
