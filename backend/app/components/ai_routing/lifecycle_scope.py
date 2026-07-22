"""Exception-safe cleanup for routed workflow entrypoints."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar, cast

from .execution import RouteExecution


logger = logging.getLogger("taali.ai_routing.lifecycle_scope")

_TRACKED_ROUTES: ContextVar[list[RouteExecution] | None] = ContextVar(
    "ai_routing_tracked_routes",
    default=None,
)
_Result = TypeVar("_Result")


def track_route(execution: RouteExecution) -> None:
    """Register a newly-started route with the current workflow guard."""

    tracked = _TRACKED_ROUTES.get()
    if tracked is not None:
        tracked.append(execution)


@contextmanager
def fail_open_routes_on_exception() -> Iterator[None]:
    """Fail any still-open routes if an unexpected workflow exception escapes."""

    tracked: list[RouteExecution] = []
    token = _TRACKED_ROUTES.set(tracked)
    try:
        yield
    except BaseException:
        for execution in reversed(tracked):
            if execution.terminal_status is not None:
                continue
            try:
                execution.finish_workflow(succeeded=False)
            except Exception:
                logger.exception(
                    "could not fail leaked route invocation=%s",
                    execution.invocation_id,
                )
        raise
    finally:
        _TRACKED_ROUTES.reset(token)


def guarded_routed_workflow(
    function: Callable[..., _Result],
) -> Callable[..., _Result]:
    """Decorate a synchronous workflow entrypoint with route cleanup."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> _Result:
        with fail_open_routes_on_exception():
            return function(*args, **kwargs)

    return cast(Callable[..., _Result], wrapped)


__all__ = [
    "fail_open_routes_on_exception",
    "guarded_routed_workflow",
    "track_route",
]
