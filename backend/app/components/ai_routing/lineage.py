"""Explicit workflow lineage for nested AI invocations.

Feature code activates a parent route only while it dispatches nested work.
Any route prepared inside that scope becomes a child invocation.  A
``ContextVar`` keeps concurrent requests and async tasks isolated without
threading routing identifiers through every existing tool signature.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from .execution import RouteExecution

_CURRENT_ROUTE: ContextVar[RouteExecution | None] = ContextVar(
    "ai_routing_current_route",
    default=None,
)


def current_route() -> RouteExecution | None:
    """Return the route active at the current nested-work boundary."""

    return _CURRENT_ROUTE.get()


def inherited_lineage() -> tuple[str, str] | None:
    """Return ``(root, parent)`` IDs for a nested route, when in scope."""

    route = current_route()
    if route is None:
        return None
    return route.decision.root_invocation_id, route.invocation_id


@contextmanager
def routing_scope(route: RouteExecution) -> Iterator[None]:
    """Make ``route`` the explicit parent of AI work dispatched in the block."""

    token = _CURRENT_ROUTE.set(route)
    try:
        yield
    finally:
        _CURRENT_ROUTE.reset(token)


__all__ = ["current_route", "inherited_lineage", "routing_scope"]
