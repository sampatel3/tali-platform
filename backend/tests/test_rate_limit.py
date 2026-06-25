"""P1 anti-abuse: shared fixed-window rate limiter (in-process fallback path)."""
import pytest

from app.services import rate_limit


@pytest.fixture(autouse=True)
def _force_memory(monkeypatch):
    # Force the in-process backend so tests are deterministic regardless of a
    # locally running Redis.
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
