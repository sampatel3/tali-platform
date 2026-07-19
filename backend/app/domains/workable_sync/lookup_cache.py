"""Resilient cache for Workable account-level lookup data."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ...components.integrations.workable.service import WorkableRateLimitError
from ...platform.config import settings

logger = logging.getLogger(__name__)

_LOOKUP_CACHE_FRESH_SECONDS = 600
_LOOKUP_CACHE_RETAIN_SECONDS = 86400


def lookup_cache_redis():
    try:
        import redis  # type: ignore

        return redis.Redis.from_url(settings.REDIS_URL)
    except Exception:
        return None


def cached_account_lookup(
    subdomain: str | None,
    kind: str,
    fetch_fn: Callable[[], Any],
    *,
    redis_factory: Callable[[], Any],
    log: logging.Logger,
    fresh_seconds: int,
    retain_seconds: int,
):
    """Cache a lookup and retain its last good value across transient 429s."""
    client = redis_factory()
    key = f"workable_lookup:{(subdomain or '').strip().lower()}:{kind}"
    cached: dict | None = None
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                    cached = parsed
        except Exception:
            cached = None

    now = time.time()
    if cached and (now - float(cached.get("synced_at") or 0)) < fresh_seconds:
        return cached["data"]

    try:
        data = fetch_fn()
    except WorkableRateLimitError:
        if cached:
            log.warning("Workable %s rate-limited; serving cached value", kind)
            return cached["data"]
        raise

    if isinstance(data, list) and data:
        if client is not None:
            try:
                client.set(
                    key,
                    json.dumps({"data": data, "synced_at": now}),
                    ex=retain_seconds,
                )
            except Exception:
                pass
        return data
    if cached:
        return cached["data"]
    return data
