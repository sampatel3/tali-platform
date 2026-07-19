"""Cooperative lease-loss signal for Workable pull syncs."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps


class WorkableSyncYielded(RuntimeError):
    """The caller can no longer safely continue this provider conversation."""


def raise_if_sync_should_yield(should_yield: Callable[[], bool] | None) -> None:
    if should_yield is not None and should_yield():
        raise WorkableSyncYielded()


def raise_if_client_should_yield(client) -> None:
    raise_if_sync_should_yield(getattr(client, "_sync_lease_observer", None))


def bind_sync_lease_observer(method: Callable) -> Callable:
    """Bind one sync's live observer to all HTTP calls made by its client."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        previous = getattr(self.client, "_sync_lease_observer", None)
        self.client._sync_lease_observer = kwargs.get("should_yield")
        try:
            return method(self, *args, **kwargs)
        finally:
            self.client._sync_lease_observer = previous

    return wrapped
