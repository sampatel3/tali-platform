"""Narrow transport overrides that never unwrap a metered Anthropic client."""

from __future__ import annotations

from typing import Any


def rewrap_with_bounded_options(
    client: Any,
    *,
    timeout: float,
    max_retries: int,
) -> Any:
    """Apply a finite deadline and disable only hidden SDK retries."""

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite positive number")
    resolved_timeout = float(timeout)
    if not 0 < resolved_timeout < float("inf"):
        raise ValueError("timeout must be a finite positive number")
    if type(max_retries) is not int or max_retries != 0:
        raise ValueError("max_retries must be 0 for a metered deadline client")
    configured = client._inner.with_options(
        timeout=resolved_timeout,
        max_retries=0,
    )
    return type(client)(
        inner=configured,
        organization_id=client._organization_id,
    )


__all__ = ["rewrap_with_bounded_options"]
