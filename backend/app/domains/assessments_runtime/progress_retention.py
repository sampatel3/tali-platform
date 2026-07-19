"""Bound process-local job progress without hiding recently finished work."""

from __future__ import annotations

from collections.abc import Hashable, MutableMapping
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, TypeVar


RECENT_TERMINAL_PROGRESS_TTL = timedelta(hours=24)
TERMINAL_PROGRESS_STATES = frozenset({"completed", "cancelled", "failed"})

_Scope = TypeVar("_Scope", bound=Hashable)
_ProgressStore = MutableMapping[_Scope, dict[str, Any]]
_PROGRESS_RETENTION_LOCK = RLock()


def _utc(value: datetime | None = None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _terminal_time(progress: dict[str, Any]) -> datetime | None:
    raw = progress.get("terminal_at")
    if isinstance(raw, datetime):
        return _utc(raw)
    if isinstance(raw, str):
        try:
            return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _stamp_status(progress: dict[str, Any], *, now: datetime) -> None:
    status = str(progress.get("status") or "").strip().lower()
    if status in TERMINAL_PROGRESS_STATES:
        if _terminal_time(progress) is None:
            progress["terminal_at"] = now
    else:
        progress.pop("terminal_at", None)


def _prune_terminal_scope(
    store: _ProgressStore[_Scope],
    scope: _Scope,
    *,
    observed_at: datetime,
    ttl: timedelta,
) -> None:
    """Evict one expired terminal entry without scanning unrelated scopes."""

    progress = store.get(scope)
    if progress is None:
        return
    status = str(progress.get("status") or "").strip().lower()
    if status not in TERMINAL_PROGRESS_STATES:
        return
    terminal_at = _terminal_time(progress)
    current = store.get(scope)
    current_status = str(progress.get("status") or "").strip().lower()
    if current is not progress or current_status not in TERMINAL_PROGRESS_STATES:
        return
    if terminal_at is None:
        # Give pre-deployment entries one bounded grace period instead of
        # making a rollout erase every visible terminal notification.
        progress["terminal_at"] = observed_at
        return
    current_status = str(progress.get("status") or "").strip().lower()
    if (
        observed_at - terminal_at >= ttl
        and store.get(scope) is progress
        and current_status in TERMINAL_PROGRESS_STATES
    ):
        store.pop(scope, None)


def prune_terminal_progress(
    store: _ProgressStore[_Scope],
    *,
    now: datetime | None = None,
    ttl: timedelta = RECENT_TERMINAL_PROGRESS_TTL,
) -> None:
    """Evict terminal entries at ``ttl`` while retaining active entries."""

    with _PROGRESS_RETENTION_LOCK:
        observed_at = _utc(now)
        for scope, _progress in list(store.items()):
            _prune_terminal_scope(
                store,
                scope,
                observed_at=observed_at,
                ttl=ttl,
            )


def set_bounded_progress(
    store: _ProgressStore[_Scope],
    scope: _Scope,
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
    ttl: timedelta = RECENT_TERMINAL_PROGRESS_TTL,
) -> None:
    """Store progress, timestamp terminal transitions, and sweep old entries."""

    with _PROGRESS_RETENTION_LOCK:
        observed_at = _utc(now)
        _stamp_status(progress, now=observed_at)
        store[scope] = progress
        prune_terminal_progress(store, now=observed_at, ttl=ttl)


def publish_active_progress(
    store: _ProgressStore[_Scope],
    scope: _Scope,
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Publish an active counter update without running an O(store) sweep."""

    with _PROGRESS_RETENTION_LOCK:
        _stamp_status(progress, now=_utc(now))
        store[scope] = progress


def retained_progress_items(
    store: _ProgressStore[_Scope],
    *,
    now: datetime | None = None,
    ttl: timedelta = RECENT_TERMINAL_PROGRESS_TTL,
) -> list[tuple[_Scope, dict[str, Any]]]:
    """Return active/recent entries after applying terminal retention."""

    with _PROGRESS_RETENTION_LOCK:
        prune_terminal_progress(store, now=now, ttl=ttl)
        return list(store.items())


def get_retained_progress(
    store: _ProgressStore[_Scope],
    scope: _Scope,
    *,
    now: datetime | None = None,
    ttl: timedelta = RECENT_TERMINAL_PROGRESS_TTL,
) -> dict[str, Any] | None:
    """Read and bound one entry without scanning unrelated scopes."""

    with _PROGRESS_RETENTION_LOCK:
        _prune_terminal_scope(store, scope, observed_at=_utc(now), ttl=ttl)
        return store.get(scope)
