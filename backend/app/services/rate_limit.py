"""Shared fixed-window rate limiter (P1 anti-abuse).

Redis-backed so the limit is enforced ACROSS replicas (the in-process
RateLimitMiddleware is useless in multi-replica prod). Falls back to an in-process
window when Redis is unreachable (degraded protection + unit tests). This gates
the public apply endpoint (per IP + role) before any DB write or LLM call.
"""
from __future__ import annotations

import threading
import time

from ..platform.config import settings

_memory_lock = threading.Lock()
# key -> (window_index, count)
_memory_buckets: dict[str, tuple[int, int]] = {}
_MEMORY_MAX_KEYS = 50_000

_redis_client = None
_redis_init_attempted = False


def _get_redis():
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    _redis_init_attempted = True
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
    window = int(time.time()) // window_seconds
    with _memory_lock:
        if len(_memory_buckets) > _MEMORY_MAX_KEYS:
            stale = [k for k, (w, _) in _memory_buckets.items() if w != window]
            for k in stale:
                _memory_buckets.pop(k, None)
        w, count = _memory_buckets.get(key, (window, 0))
        if w != window:
            w, count = window, 0
        count += 1
        _memory_buckets[key] = (w, count)
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
    with _memory_lock:
        _memory_buckets.clear()
