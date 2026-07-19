"""Shared fixed-window rate limiter (P1 anti-abuse).

Redis-backed so the limit is enforced ACROSS replicas (a purely in-process
window is useless in multi-replica prod). Falls back to an in-process window
when Redis is unreachable (degraded protection + unit tests). This is the
shared limiter later slices gate the public apply endpoint with (per IP +
role) before any DB write or LLM call.

Redis init is retried after a cooldown: if Redis is unreachable on the first
call the limiter degrades to in-process, but re-attempts the connection no more
than once per ``_REDIS_RETRY_COOLDOWN_SECONDS`` so a transient Redis blip
doesn't leave the process permanently degraded.
"""
from __future__ import annotations

import threading
import time

from ..platform.config import settings

_memory_lock = threading.Lock()
# key -> (fixed-window expiry epoch, count).  Expiry, rather than a bare
# window index, is important because callers use both minute and hourly
# windows; indices from different durations are not comparable.
_memory_buckets: dict[str, tuple[float, int]] = {}
_MEMORY_MAX_KEYS = 50_000
_memory_next_expiry: float | None = None

_redis_client = None
# Monotonic timestamp of the last init attempt; None means "never tried".
_redis_last_attempt: float | None = None
_REDIS_RETRY_COOLDOWN_SECONDS = 60.0
_redis_lock = threading.Lock()


def _get_redis():
    """Return a live Redis client, or None if Redis is unavailable.

    Caches a working client. When Redis is unreachable, degrades to None but
    re-attempts the connection at most once per cooldown window so a Redis blip
    doesn't permanently degrade the limiter.
    """
    global _redis_client, _redis_last_attempt
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        now = time.monotonic()
        if (
            _redis_last_attempt is not None
            and now - _redis_last_attempt < _REDIS_RETRY_COOLDOWN_SECONDS
        ):
            return None
        _redis_last_attempt = now
        url = getattr(settings, "REDIS_URL", None)
        if not url:
            return None
        try:  # pragma: no cover - exercised only when a real Redis is present
            import redis  # type: ignore

            client = redis.Redis.from_url(
                url, socket_connect_timeout=0.5, socket_timeout=0.5
            )
            client.ping()
            _redis_client = client
        except Exception:
            _redis_client = None
        return _redis_client


def _check_memory(key: str, limit: int, window_seconds: int) -> bool:
    global _memory_next_expiry
    now = time.time()
    window = int(now) // window_seconds
    expires_at = float((window + 1) * window_seconds)
    with _memory_lock:
        current = _memory_buckets.get(key)
        if current is not None:
            current_expiry, count = current
            if current_expiry <= now:
                count = 0
            else:
                expires_at = current_expiry
        else:
            count = 0

            # Only sweep when capacity is reached and the earliest known entry
            # can actually be stale. Under a same-window key flood this avoids
            # the previous O(50k) scan on every request.
            if (
                len(_memory_buckets) >= _MEMORY_MAX_KEYS
                and _memory_next_expiry is not None
                and _memory_next_expiry <= now
            ):
                stale = [
                    bucket_key
                    for bucket_key, (bucket_expiry, _) in _memory_buckets.items()
                    if bucket_expiry <= now
                ]
                for bucket_key in stale:
                    _memory_buckets.pop(bucket_key, None)
                _memory_next_expiry = min(
                    (bucket_expiry for bucket_expiry, _ in _memory_buckets.values()),
                    default=None,
                )

            # Redis is already unavailable on this path. Refuse a brand-new
            # bucket once the bounded fallback is full instead of allocating
            # without limit (or evicting an active bucket and weakening abuse
            # protection). Existing keys continue to use their own budgets.
            if len(_memory_buckets) >= _MEMORY_MAX_KEYS:
                return False

        count += 1
        _memory_buckets[key] = (expires_at, count)
        if _memory_next_expiry is None or expires_at < _memory_next_expiry:
            _memory_next_expiry = expires_at
        return count <= limit


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> bool:
    """Return True if the action for ``key`` is within ``limit`` for the current
    fixed window, False if the limit is exceeded. Counts the call. Uses Redis when
    available, else an in-process fallback."""
    if limit <= 0:
        return False
    client = _get_redis()
    if client is not None:
        try:  # pragma: no cover - requires a live Redis
            window = int(time.time()) // window_seconds
            full_key = f"ratelimit:{key}:{window}"
            count = client.incr(full_key)
            if count == 1:
                client.expire(full_key, window_seconds)
            return int(count) <= limit
        except Exception:
            pass  # Redis hiccup -> degrade to in-process
    return _check_memory(key, limit, window_seconds)


def reset_memory_buckets() -> None:
    """Test helper: clear the in-process window state."""
    global _memory_next_expiry
    with _memory_lock:
        _memory_buckets.clear()
        _memory_next_expiry = None


def reset_redis_state() -> None:
    """Test helper: forget the cached client and the last-attempt timestamp so
    the next ``_get_redis`` re-attempts init immediately."""
    global _redis_client, _redis_last_attempt
    with _redis_lock:
        _redis_client = None
        _redis_last_attempt = None
