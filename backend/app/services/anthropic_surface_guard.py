"""Fail-closed facades for Anthropic SDK surfaces we do not meter.

The SDK exposes alternative response wrappers and whole secondary resources
through attributes.  Forwarding those attributes from a metered client makes
it possible to execute a paid request without the reservation and settlement
logic in ``messages.create``.  Keep the allowlist deliberately narrow: model
metadata and token counting are local/non-billable operations; every other
unknown surface must be integrated with metering before it is exposed.
"""

from __future__ import annotations

from typing import Any


class UnsupportedAnthropicSurfaceError(RuntimeError):
    """The requested SDK surface has no complete metering implementation."""


class NonbillableAnthropicResource:
    """Expose only explicitly reviewed non-billable resource operations."""

    def __init__(self, *, inner: Any, allowed_operations: frozenset[str]):
        self._inner = inner
        self._allowed_operations = allowed_operations

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._allowed_operations:
            raise UnsupportedAnthropicSurfaceError(
                "Anthropic SDK operation is unavailable until metering is implemented"
            )
        return getattr(self._inner, name)


NONBILLABLE_MODEL_OPERATIONS = frozenset({"list", "retrieve"})
NONBILLABLE_MESSAGE_OPERATIONS = frozenset({"count_tokens"})
# Shared-account list/cancel can disclose or mutate another tenant's batches.
# Retrieve/results are exposed only by the metered adapter after local ownership.
NONBILLABLE_BATCH_OPERATIONS = frozenset()


__all__ = [
    "NONBILLABLE_BATCH_OPERATIONS",
    "NONBILLABLE_MESSAGE_OPERATIONS",
    "NONBILLABLE_MODEL_OPERATIONS",
    "NonbillableAnthropicResource",
    "UnsupportedAnthropicSurfaceError",
]
