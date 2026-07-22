"""Validation primitives for content-free AI routing telemetry."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

_FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    "assistant_prompt candidate_content content contents cv_text document_text "
    "input inputs message messages prompt prompts raw_input system_prompt text "
    "user_prompt".split()
)
_MAX_SNAPSHOT_BYTES = 128 * 1024
_REASON_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class AIRoutingTelemetryError(ValueError):
    """Base class for rejected telemetry writes."""


class AIRoutingIdempotencyConflict(AIRoutingTelemetryError):
    """A stable key was reused with different immutable data."""


class AIRoutingStatusTransitionError(AIRoutingTelemetryError):
    """A lifecycle transition violated the routing state machine."""


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, set):
        return sorted(value, key=repr)
    raise TypeError(f"Unsupported routing snapshot value: {type(value).__name__}")


def _reject_content(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AIRoutingTelemetryError("Routing snapshot keys must be strings")
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_SNAPSHOT_KEYS:
                raise AIRoutingTelemetryError(
                    f"forbidden routing snapshot field: {key}"
                )
            _reject_content(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _reject_content(item)


def json_safe_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, bounded JSON object suitable for persistence."""

    if not isinstance(value, Mapping):
        raise AIRoutingTelemetryError("Routing snapshots must be mappings")
    _reject_content(value)
    try:
        encoded = json.dumps(
            value,
            default=_json_default,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except ValueError as exc:
        raise AIRoutingTelemetryError(
            "Routing snapshots cannot contain NaN or infinity"
        ) from exc
    except TypeError as exc:
        raise AIRoutingTelemetryError(str(exc)) from exc
    if len(encoded.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise AIRoutingTelemetryError("Routing snapshot exceeds the 128 KiB limit")
    return json.loads(encoded)


def uuid_string(value: str | UUID | None, *, field: str, generate: bool) -> str:
    if value is None:
        if generate:
            return str(uuid4())
        raise AIRoutingTelemetryError(f"{field} is required")
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AIRoutingTelemetryError(f"{field} must be a UUID string") from exc


def required_text(value: str, *, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AIRoutingTelemetryError(f"{field} must be a non-empty string")
    if len(value) > max_length:
        raise AIRoutingTelemetryError(f"{field} exceeds {max_length} characters")
    return value


def optional_text(
    value: str | None,
    *,
    field: str,
    max_length: int,
) -> str | None:
    return (
        None
        if value is None
        else required_text(value, field=field, max_length=max_length)
    )


def reason(value: str | None, *, field: str) -> str | None:
    value = optional_text(value, field=field, max_length=160)
    if value is not None and _REASON_CODE.fullmatch(value) is None:
        raise AIRoutingTelemetryError(
            f"{field} must be a machine-readable reason code, not free text"
        )
    return value


def optional_id(value: int | None, *, field: str) -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise AIRoutingTelemetryError(f"{field} must be a positive integer")
    return value


def attempt_ordinal(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AIRoutingTelemetryError("Attempt ordinal must be a positive integer")
    return value


def nonnegative_int(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AIRoutingTelemetryError(
            f"{field} must be a non-negative integer"
        )
    return value


def positive_int(value: int, *, field: str) -> int:
    value = nonnegative_int(value, field=field)
    if value == 0:
        raise AIRoutingTelemetryError(f"{field} must be a positive integer")
    return value


def timestamp(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise AIRoutingTelemetryError("Telemetry timestamps must be timezone-aware")
    return value


def require_same(row: Any, expected: Mapping[str, Any], *, key: str) -> None:
    mismatched = [
        field for field, value in expected.items() if getattr(row, field) != value
    ]
    if mismatched:
        fields = ", ".join(sorted(mismatched))
        raise AIRoutingIdempotencyConflict(
            f"{key} was reused with different fields: {fields}"
        )


__all__ = [
    "AIRoutingIdempotencyConflict",
    "AIRoutingStatusTransitionError",
    "AIRoutingTelemetryError",
    "attempt_ordinal",
    "json_safe_snapshot",
    "nonnegative_int",
    "optional_id",
    "optional_text",
    "positive_int",
    "reason",
    "require_same",
    "required_text",
    "timestamp",
    "uuid_string",
]
