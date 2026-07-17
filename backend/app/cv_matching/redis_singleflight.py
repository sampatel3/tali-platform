"""Small Redis-backed single-flight for deterministic paid computations."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from threading import Event, Thread
from typing import Any, TypeVar


logger = logging.getLogger(__name__)
T = TypeVar("T")
_MISS = object()
_RELEASE_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_EXTEND_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_PUBLISH_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('setex', KEYS[2], ARGV[2], ARGV[3])
end
return 0
"""


class RedisSingleFlightBusy(RuntimeError):
    """The shared computation is still owned by another live worker."""


def _read_cached(client: Any, key: str, deserialize: Callable[[Any], T]) -> T | object:
    raw = client.get(key)
    if raw is None:
        return _MISS
    try:
        return deserialize(raw)
    except Exception:
        logger.warning("single-flight cache value is invalid key=%s", key, exc_info=True)
        return _MISS


def redis_singleflight(
    client: Any | None,
    *,
    key: str,
    compute: Callable[[], T],
    deserialize: Callable[[Any], T],
    serialize: Callable[[T], str | bytes],
    cacheable: Callable[[T], bool],
    success_ttl_seconds: int,
    empty_ttl_seconds: int,
    lock_ttl_seconds: int = 600,
    wait_timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 0.1,
) -> T:
    """Return one shared computation while preserving a bounded fallback.

    A short-lived cache entry for an empty/failed value prevents a provider
    outage from turning one cold role into a worker stampede. A configured
    Redis client is also the ownership authority, not merely a cache: Redis
    faults fail closed with :class:`RedisSingleFlightBusy` so an outage cannot
    turn every caller into a duplicate paid computation. Passing
    ``client=None`` remains the explicit opt-out for environments without
    shared coordination.
    """

    if client is None:
        return compute()
    try:
        cached = _read_cached(client, key, deserialize)
    except Exception:
        logger.warning("single-flight cache read failed key=%s", key, exc_info=True)
        raise RedisSingleFlightBusy(
            f"single-flight ownership unavailable for {key}"
        ) from None
    if cached is not _MISS:
        return cached  # type: ignore[return-value]

    lock_key = f"{key}:singleflight"
    token = uuid.uuid4().hex

    def try_acquire() -> bool:
        return bool(
            client.set(
                lock_key,
                token,
                nx=True,
                ex=max(1, int(lock_ttl_seconds)),
            )
        )

    try:
        owns_lock = try_acquire()
    except Exception:
        logger.warning("single-flight lock acquire failed key=%s", key, exc_info=True)
        raise RedisSingleFlightBusy(
            f"single-flight ownership unavailable for {key}"
        ) from None

    if not owns_lock:
        deadline = time.monotonic() + max(0.0, float(wait_timeout_seconds))
        while time.monotonic() < deadline:
            time.sleep(max(0.001, float(poll_interval_seconds)))
            try:
                cached = _read_cached(client, key, deserialize)
                if cached is not _MISS:
                    return cached  # type: ignore[return-value]
                owns_lock = try_acquire()
            except Exception:
                logger.warning("single-flight follower failed key=%s", key, exc_info=True)
                raise RedisSingleFlightBusy(
                    f"single-flight ownership unavailable for {key}"
                ) from None
            if owns_lock:
                break

    if not owns_lock:
        try:
            cached = _read_cached(client, key, deserialize)
        except Exception:
            logger.warning(
                "single-flight final cache read failed key=%s", key, exc_info=True
            )
            raise RedisSingleFlightBusy(
                f"single-flight ownership unavailable for {key}"
            ) from None
        if cached is not _MISS:
            return cached  # type: ignore[return-value]
        # The lease can expire between the loop's last poll and this final
        # check. Only a successful SET NX authorizes this worker to compute.
        try:
            owns_lock = try_acquire()
        except Exception:
            logger.warning(
                "single-flight final lock acquire failed key=%s", key, exc_info=True
            )
            raise RedisSingleFlightBusy(
                f"single-flight ownership unavailable for {key}"
            ) from None
        if not owns_lock:
            logger.warning("single-flight wait timed out with live owner key=%s", key)
            raise RedisSingleFlightBusy(f"single-flight computation busy for {key}")

    heartbeat_stop: Event | None = None
    heartbeat_thread: Thread | None = None
    release_lock = True
    try:
        # A previous leader can publish and release between this caller's last
        # read and successful SET NX. Recheck while owning the lock so that
        # hand-off race cannot trigger a duplicate paid computation.
        try:
            cached = _read_cached(client, key, deserialize)
        except Exception:
            logger.warning(
                "single-flight owned cache recheck failed key=%s", key, exc_info=True
            )
            raise RedisSingleFlightBusy(
                f"single-flight ownership unavailable for {key}"
            ) from None
        if cached is not _MISS:
            return cached  # type: ignore[return-value]

        # Verify the token synchronously immediately before starting paid work.
        # This closes the acquire/recheck hand-off window and refreshes the full
        # lease before a provider call. The compare-and-expire script can never
        # bless a replacement owner's token.
        try:
            owns_lock = bool(
                client.eval(
                    _EXTEND_IF_OWNED,
                    1,
                    lock_key,
                    token,
                    max(1, int(lock_ttl_seconds)),
                )
            )
        except Exception:
            logger.warning(
                "single-flight ownership verification failed key=%s",
                key,
                exc_info=True,
            )
            raise RedisSingleFlightBusy(
                f"single-flight ownership unavailable for {key}"
            ) from None
        if not owns_lock:
            release_lock = False
            logger.warning("single-flight ownership lost before compute key=%s", key)
            raise RedisSingleFlightBusy(f"single-flight computation busy for {key}")

        # A structured generation can make a validation-retry call after a
        # successful-but-malformed response. Keep the lease alive throughout
        # that bounded provider work so it cannot expire underneath the leader.
        # The compare-and-expire script can never extend a successor's lease.
        heartbeat_stop = Event()
        heartbeat_interval = max(
            0.1,
            min(float(max(1, int(lock_ttl_seconds))) / 3.0, 60.0),
        )

        def renew_lease() -> None:
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    renewed = client.eval(
                        _EXTEND_IF_OWNED,
                        1,
                        lock_key,
                        token,
                        max(1, int(lock_ttl_seconds)),
                    )
                except Exception:
                    logger.warning(
                        "single-flight lease renewal failed key=%s",
                        key,
                        exc_info=True,
                    )
                    continue
                if not renewed:
                    logger.warning("single-flight lease ownership lost key=%s", key)
                    return

        heartbeat_thread = Thread(
            target=renew_lease,
            name="redis-singleflight-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        value = compute()

        # Publish only while this token still owns the lease. A plain SETEX
        # after a hand-off lets an expired leader overwrite its successor and
        # makes two paid generations look like one successful flight. If the
        # write is unavailable or ambiguous, retain the lease until its TTL
        # instead of releasing it into an immediate recomputation stampede.
        try:
            ttl = success_ttl_seconds if cacheable(value) else empty_ttl_seconds
            payload = serialize(value)
            published = bool(
                client.eval(
                    _PUBLISH_IF_OWNED,
                    2,
                    lock_key,
                    key,
                    token,
                    max(1, int(ttl)),
                    payload,
                )
            )
        except Exception:
            logger.warning("single-flight cache write failed key=%s", key, exc_info=True)
            release_lock = False
        else:
            if not published:
                release_lock = False
                logger.warning(
                    "single-flight cache publish skipped after ownership loss key=%s",
                    key,
                )
                # The paid result cannot become the shared authoritative value.
                # Stop here so this stale leader also cannot launch downstream
                # paid work from a derivation that differs from the successor's.
                raise RedisSingleFlightBusy(
                    f"single-flight ownership lost while publishing {key}"
                )
        return value
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
        if release_lock:
            try:
                client.eval(_RELEASE_IF_OWNED, 1, lock_key, token)
            except Exception:
                # Never issue a non-atomic GET+DELETE fallback: the lease may have
                # expired and been acquired by another worker in between.
                logger.warning(
                    "single-flight lock release failed key=%s", key, exc_info=True
                )


__all__ = ["RedisSingleFlightBusy", "redis_singleflight"]
