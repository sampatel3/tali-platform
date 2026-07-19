"""Token-shape helpers shared by sync and async Anthropic metering."""

from __future__ import annotations

from typing import Any


def extract_cache_creation_1h(usage: Any) -> int | None:
    """Return the one-hour prompt-cache write count when the SDK supplies it."""

    if usage is None:
        return None
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is None:
        return None
    value = getattr(cache_creation, "ephemeral_1h_input_tokens", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["extract_cache_creation_1h"]
