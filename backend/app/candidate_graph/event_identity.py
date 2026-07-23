"""Canonical logical-role identity for candidate application events."""

from __future__ import annotations

from numbers import Integral
from typing import Any


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (Integral, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def logical_event_role_id(event: Any) -> int | None:
    """Return the role that owns an event.

    First-class event provenance is authoritative. A NULL legacy role is
    ambiguous at this provider boundary and therefore fails closed; the
    physical application's role is evidence transport, not logical ownership.
    """

    return _positive_int(getattr(event, "role_id", None))


__all__ = ["logical_event_role_id"]
