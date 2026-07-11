"""P1 anti-abuse: shared fixed-window rate limiter (in-process fallback path)."""
import pytest

from app.services import rate_limit


@pytest.fixture(autouse=True)
def _force_memory(monkeypatch):
    # Force the in-process backend so the fixed-window tests are deterministic
    # regardless of an ambient reachable Redis (which would otherwise make the
    # counts nondeterministic across runs).
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    rate_limit.reset_memory_buckets()


def test_fixed_window_allows_up_to_limit_then_blocks():
    results = [
        rate_limit.check_rate_limit("ip1:roleA", limit=3, window_seconds=60)
        for _ in range(5)
    ]
    assert results == [True, True, True, False, False]


def test_separate_keys_are_independent():
    assert rate_limit.check_rate_limit("a", limit=1, window_seconds=60) is True
    assert rate_limit.check_rate_limit("a", limit=1, window_seconds=60) is False
    assert rate_limit.check_rate_limit("b", limit=1, window_seconds=60) is True


def test_zero_limit_blocks():
    assert rate_limit.check_rate_limit("x", limit=0, window_seconds=60) is False


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_redis_init_retries_after_cooldown(monkeypatch):
    """A Redis blip must not permanently degrade the limiter: after the cooldown
    elapses, ``_get_redis`` re-attempts the connection. Uses a fake monotonic
    clock so no real time passes."""
    # Don't let the autouse fixture stub out the getter we're exercising.
    monkeypatch.undo()
    rate_limit.reset_redis_state()

    clock = _FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(rate_limit.settings, "REDIS_URL", "redis://unreachable:6379")

    attempts = {"n": 0}

    class _BoomRedis:
        @staticmethod
        def from_url(*args, **kwargs):
            attempts["n"] += 1
            raise ConnectionError("redis down")

    import sys
    import types

    fake_redis_module = types.SimpleNamespace(Redis=_BoomRedis)
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)

    # First call: attempts init, fails, degrades to None.
    assert rate_limit._get_redis() is None
    assert attempts["n"] == 1

    # Within the cooldown: no re-attempt.
    clock.advance(rate_limit._REDIS_RETRY_COOLDOWN_SECONDS - 1)
    assert rate_limit._get_redis() is None
    assert attempts["n"] == 1

    # Past the cooldown: re-attempts (still fails here, but the point is it tries).
    clock.advance(2)
    assert rate_limit._get_redis() is None
    assert attempts["n"] == 2

    rate_limit.reset_redis_state()


def test_no_redis_url_is_never_a_permanent_latch(monkeypatch):
    """With no REDIS_URL configured, each cooldown boundary still re-checks
    (so setting REDIS_URL later would take effect) rather than latching once."""
    monkeypatch.undo()
    rate_limit.reset_redis_state()

    clock = _FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(rate_limit.settings, "REDIS_URL", None)

    assert rate_limit._get_redis() is None
    clock.advance(rate_limit._REDIS_RETRY_COOLDOWN_SECONDS + 1)
    assert rate_limit._get_redis() is None

    rate_limit.reset_redis_state()
