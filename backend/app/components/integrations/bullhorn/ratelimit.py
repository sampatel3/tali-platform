"""Rate limiting + 429 backoff + circuit breaker for the Bullhorn client.

Bullhorn shares one budget across a whole ``client_id``: 1,500 req/min, and
—critically— **>=9,000 429s in a rolling 5 min can DISABLE the API user**. So
this is stricter than Workable's per-token pacing: a token bucket kept an order
of magnitude under the req/min ceiling (default ~5 req/s), plus a hard circuit
breaker that opens well before the disable threshold (~500 429s / rolling 5 min,
~18x under the 9,000 limit) so a runaway can never brick the customer's API user.

All three pieces are pure, deterministic logic (clock + sleep injected) so they
unit-test without a network. One limiter instance is shared per ``client_id``
(process-global, same rationale as the Workable per-subdomain limiter): a single
org sync fans out across threads that share the one Bullhorn budget.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import httpx

# Token bucket: refill rate + burst capacity. 5 req/s * 60 = 300 req/min, ~5x
# under Bullhorn's 1,500 req/min so cross-process callers on the same client_id
# still have headroom.
BULLHORN_RATE_PER_SEC = 5.0
BULLHORN_RATE_BURST = 10

# 429 backoff: honor Retry-After when present, else exponential. Bounded so a
# wedged token can't hang a sync forever.
BULLHORN_MAX_ATTEMPTS = 4
BULLHORN_BACKOFF_BASE_SEC = 1.0
BULLHORN_BACKOFF_CAP_SEC = 30.0

# Circuit breaker: if this many 429s land inside the rolling window, open the
# breaker and fail fast. 500 / 5 min is ~18x under Bullhorn's 9,000-in-5-min
# user-disable threshold.
BULLHORN_BREAKER_WINDOW_SEC = 300.0
BULLHORN_BREAKER_MAX_429 = 500


def retry_after_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait before retrying a 429: honor Retry-After, else backoff."""
    header = response.headers.get("Retry-After") if response is not None else None
    if header:
        try:
            return min(float(header), BULLHORN_BACKOFF_CAP_SEC)
        except (TypeError, ValueError):
            pass  # Retry-After may be an HTTP-date — fall through to backoff
    return min(
        BULLHORN_BACKOFF_BASE_SEC * (2 ** max(0, attempt)), BULLHORN_BACKOFF_CAP_SEC
    )


class TokenBucket:
    """Process-global token bucket, one instance per Bullhorn ``client_id``.

    ``acquire`` blocks until a token is available. Refills continuously at
    ``rate_per_sec`` up to ``burst`` capacity. Clock + sleep are injectable so
    the pacing is testable without wall-clock waits.
    """

    def __init__(
        self,
        rate_per_sec: float = BULLHORN_RATE_PER_SEC,
        burst: int = BULLHORN_RATE_BURST,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._rate = max(0.001, float(rate_per_sec))
        self._capacity = float(max(1, int(burst)))
        self._tokens = self._capacity
        self._last = monotonic()
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()

    def _refill_locked(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._monotonic()
                self._refill_locked(now)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self._rate
            if wait > 0:
                self._sleep(wait)


class CircuitBreaker:
    """Rolling-window 429 counter that opens before the user-disable threshold.

    ``record_429`` stamps a 429; ``check`` raises via ``on_open`` once the count
    inside the rolling window crosses ``max_429``. Clock injectable for tests.
    """

    def __init__(
        self,
        max_429: int = BULLHORN_BREAKER_MAX_429,
        window_sec: float = BULLHORN_BREAKER_WINDOW_SEC,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._max = max(1, int(max_429))
        self._window = float(window_sec)
        self._hits: deque[float] = deque()
        self._monotonic = monotonic
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._window
        while self._hits and self._hits[0] <= cutoff:
            self._hits.popleft()

    def record_429(self) -> None:
        with self._lock:
            now = self._monotonic()
            self._prune_locked(now)
            self._hits.append(now)

    def is_open(self) -> bool:
        with self._lock:
            now = self._monotonic()
            self._prune_locked(now)
            return len(self._hits) >= self._max


_buckets: dict[str, TokenBucket] = {}
_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_bucket(client_id: str) -> TokenBucket:
    """Return the shared token bucket for a ``client_id`` (one budget per client)."""
    key = (client_id or "").strip()
    with _registry_lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = TokenBucket()
            _buckets[key] = bucket
        return bucket


def get_breaker(client_id: str) -> CircuitBreaker:
    """Return the shared 429 circuit breaker for a ``client_id``."""
    key = (client_id or "").strip()
    with _registry_lock:
        breaker = _breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker()
            _breakers[key] = breaker
        return breaker
