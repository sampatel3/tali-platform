from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from app.cv_matching.redis_singleflight import (
    RedisSingleFlightBusy,
    redis_singleflight,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._guard = Lock()
        self._values: dict[str, str | bytes] = {}
        self.heartbeat_extended = Event()
        self.lease_extensions = 0

    def get(self, key: str):
        with self._guard:
            return self._values.get(key)

    def set(self, key: str, value: str, *, nx: bool, ex: int):
        del ex
        with self._guard:
            if nx and key in self._values:
                return False
            self._values[key] = value
            return True

    def eval(
        self,
        script: str,
        _keys: int,
        *values,
    ) -> int:
        with self._guard:
            if "setex" in script:
                assert _keys == 2
                key, cache_key, token, ttl, payload = values
                if self._values.get(key) != token:
                    return 0
                assert int(ttl) > 0
                self._values[cache_key] = payload
                return 1

            assert _keys == 1
            key, token, *args = values
            if self._values.get(key) != token:
                return 0
            if "expire" in script:
                assert len(args) == 1
                assert int(args[0]) > 0
                self.lease_extensions += 1
                if self.lease_extensions >= 2:
                    self.heartbeat_extended.set()
                return 1
            del self._values[key]
            return 1


def _run_concurrent(*, computed_value: dict, cacheable) -> tuple[list[dict], int]:
    redis_client = _FakeRedis()
    leader_started = Event()
    release_leader = Event()
    compute_guard = Lock()
    compute_count = 0

    def compute() -> dict:
        nonlocal compute_count
        with compute_guard:
            compute_count += 1
        leader_started.set()
        assert release_leader.wait(timeout=2)
        return computed_value

    def call() -> dict:
        return redis_singleflight(
            redis_client,
            key="requirements:one-role",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=cacheable,
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            lock_ttl_seconds=60,
            wait_timeout_seconds=2,
            poll_interval_seconds=0.005,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(call) for _ in range(6)]
        assert leader_started.wait(timeout=2)
        release_leader.set()
        results = [future.result(timeout=2) for future in futures]
    return results, compute_count


def test_redis_singleflight_computes_a_cold_value_once_for_concurrent_callers():
    results, compute_count = _run_concurrent(
        computed_value={"requirements": ["Python"]},
        cacheable=lambda value: bool(value["requirements"]),
    )

    assert compute_count == 1
    assert results == [{"requirements": ["Python"]}] * 6


def test_redis_singleflight_briefly_shares_an_empty_result_after_provider_failure():
    results, compute_count = _run_concurrent(
        computed_value={"requirements": []},
        cacheable=lambda value: bool(value["requirements"]),
    )

    assert compute_count == 1
    assert results == [{"requirements": []}] * 6


def test_redis_singleflight_none_client_is_the_explicit_uncoordinated_mode():
    compute_count = 0

    def compute() -> dict:
        nonlocal compute_count
        compute_count += 1
        return {"requirements": ["Python"]}

    result = redis_singleflight(
        None,
        key="requirements:no-redis",
        compute=compute,
        deserialize=json.loads,
        serialize=json.dumps,
        cacheable=lambda value: bool(value["requirements"]),
        success_ttl_seconds=3600,
        empty_ttl_seconds=15,
    )

    assert result == {"requirements": ["Python"]}
    assert compute_count == 1


def test_redis_singleflight_releases_only_the_lock_token_it_owns():
    redis_client = _FakeRedis()
    lock_key = "requirements:one-role:singleflight"

    def compute() -> dict:
        with redis_client._guard:
            redis_client._values[lock_key] = "new-owner"
            redis_client._values["requirements:one-role"] = json.dumps(
                {"requirements": ["successor"]}
            )
        return {"requirements": ["Python"]}

    with pytest.raises(RedisSingleFlightBusy):
        redis_singleflight(
            redis_client,
            key="requirements:one-role",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            lock_ttl_seconds=60,
            wait_timeout_seconds=2,
            poll_interval_seconds=0.005,
        )

    assert redis_client.get(lock_key) == "new-owner"
    assert json.loads(redis_client.get("requirements:one-role")) == {
        "requirements": ["successor"]
    }


def test_redis_singleflight_timeout_never_duplicates_a_live_leader():
    redis_client = _FakeRedis()
    leader_started = Event()
    release_leader = Event()
    follower_compute_count = 0

    def leader_compute() -> dict:
        leader_started.set()
        assert release_leader.wait(timeout=2)
        return {"requirements": ["Python"]}

    def follower_compute() -> dict:
        nonlocal follower_compute_count
        follower_compute_count += 1
        return {"requirements": ["duplicate"]}

    def call(compute, *, wait_timeout_seconds: float) -> dict:
        return redis_singleflight(
            redis_client,
            key="requirements:one-role",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            lock_ttl_seconds=60,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=0.005,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(call, leader_compute, wait_timeout_seconds=2)
        assert leader_started.wait(timeout=2)
        try:
            wait_started = time.monotonic()
            with pytest.raises(RedisSingleFlightBusy):
                call(follower_compute, wait_timeout_seconds=0.02)
            assert time.monotonic() - wait_started < 1.0
            assert follower_compute_count == 0
        finally:
            release_leader.set()
        assert leader.result(timeout=2) == {"requirements": ["Python"]}


def test_redis_singleflight_renews_the_live_leader_lease_during_slow_work():
    redis_client = _FakeRedis()

    def compute() -> dict:
        # The first extension is the synchronous pre-compute ownership check;
        # the second proves the background heartbeat renewed slow work.
        assert redis_client.heartbeat_extended.wait(timeout=2)
        return {"requirements": ["Python"]}

    result = redis_singleflight(
        redis_client,
        key="requirements:one-role",
        compute=compute,
        deserialize=json.loads,
        serialize=json.dumps,
        cacheable=lambda value: bool(value["requirements"]),
        success_ttl_seconds=3600,
        empty_ttl_seconds=15,
        lock_ttl_seconds=1,
        wait_timeout_seconds=2,
        poll_interval_seconds=0.005,
    )

    assert result == {"requirements": ["Python"]}


def test_redis_singleflight_validation_retry_stays_inside_the_leader_lease():
    redis_client = _FakeRedis()
    first_attempt_started = Event()
    paid_attempts = 0
    follower_attempts = 0

    def leader_compute() -> dict:
        nonlocal paid_attempts
        paid_attempts += 1
        first_attempt_started.set()
        # Model a slow first response followed by the structured validator's
        # one allowed correction request after the heartbeat has renewed.
        assert redis_client.heartbeat_extended.wait(timeout=2)
        paid_attempts += 1
        return {"requirements": ["Python"]}

    def follower_compute() -> dict:
        nonlocal follower_attempts
        follower_attempts += 1
        return {"requirements": ["duplicate"]}

    def call(compute, wait_timeout: float) -> dict:
        return redis_singleflight(
            redis_client,
            key="requirements:validation-retry",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            lock_ttl_seconds=1,
            wait_timeout_seconds=wait_timeout,
            poll_interval_seconds=0.005,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(call, leader_compute, 2)
        assert first_attempt_started.wait(timeout=2)
        with pytest.raises(RedisSingleFlightBusy):
            call(follower_compute, 0)
        assert leader.result(timeout=2) == {"requirements": ["Python"]}

    assert paid_attempts == 2
    assert follower_attempts == 0


class _InitialReadFailureRedis(_FakeRedis):
    def get(self, key: str):
        del key
        raise ConnectionError("redis unavailable")


class _AcquireFailureRedis(_FakeRedis):
    def set(self, key: str, value: str, *, nx: bool, ex: int):
        del key, value, nx, ex
        raise ConnectionError("redis unavailable")


class _OwnedRecheckFailureRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    def get(self, key: str):
        self.get_calls += 1
        if self.get_calls == 2:
            raise ConnectionError("redis unavailable after acquire")
        return super().get(key)


class _PublishFailureRedis(_FakeRedis):
    def eval(self, script: str, _keys: int, *values) -> int:
        if "setex" in script:
            raise ConnectionError("cache publish unavailable")
        return super().eval(script, _keys, *values)


class _OwnershipLostBeforeComputeRedis(_FakeRedis):
    def eval(self, script: str, _keys: int, *values) -> int:
        if "expire" in script:
            key = values[0]
            with self._guard:
                self._values[key] = "new-owner"
            return 0
        return super().eval(script, _keys, *values)


class _PublishBeforeAcquireRedis(_FakeRedis):
    def set(self, key: str, value: str, *, nx: bool, ex: int):
        with self._guard:
            self._values["requirements:handoff"] = json.dumps(
                {"requirements": ["leader"]}
            )
        return super().set(key, value, nx=nx, ex=ex)


@pytest.mark.parametrize(
    "redis_client",
    [_InitialReadFailureRedis(), _AcquireFailureRedis(), _OwnedRecheckFailureRedis()],
)
def test_redis_singleflight_redis_faults_never_fall_through_to_paid_compute(
    redis_client,
):
    compute_count = 0

    def compute() -> dict:
        nonlocal compute_count
        compute_count += 1
        return {"requirements": ["duplicate"]}

    with pytest.raises(RedisSingleFlightBusy):
        redis_singleflight(
            redis_client,
            key="requirements:redis-fault",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            wait_timeout_seconds=0,
        )

    assert compute_count == 0


def test_redis_singleflight_token_replacement_before_compute_fails_closed():
    redis_client = _OwnershipLostBeforeComputeRedis()
    compute_count = 0

    def compute() -> dict:
        nonlocal compute_count
        compute_count += 1
        return {"requirements": ["duplicate"]}

    with pytest.raises(RedisSingleFlightBusy):
        redis_singleflight(
            redis_client,
            key="requirements:token-replaced",
            compute=compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            wait_timeout_seconds=0,
        )

    assert compute_count == 0
    assert redis_client.get("requirements:token-replaced:singleflight") == "new-owner"


def test_redis_singleflight_rechecks_cache_after_lock_handoff_before_compute():
    redis_client = _PublishBeforeAcquireRedis()
    compute_count = 0

    def compute() -> dict:
        nonlocal compute_count
        compute_count += 1
        return {"requirements": ["duplicate"]}

    result = redis_singleflight(
        redis_client,
        key="requirements:handoff",
        compute=compute,
        deserialize=json.loads,
        serialize=json.dumps,
        cacheable=lambda value: bool(value["requirements"]),
        success_ttl_seconds=3600,
        empty_ttl_seconds=15,
        wait_timeout_seconds=0,
    )

    assert result == {"requirements": ["leader"]}
    assert compute_count == 0
    assert redis_client.get("requirements:handoff:singleflight") is None


def test_redis_singleflight_cache_write_failure_retains_lease_and_blocks_recompute():
    redis_client = _PublishFailureRedis()
    follower_compute_count = 0

    result = redis_singleflight(
        redis_client,
        key="requirements:publish-failure",
        compute=lambda: {"requirements": ["Python"]},
        deserialize=json.loads,
        serialize=json.dumps,
        cacheable=lambda value: bool(value["requirements"]),
        success_ttl_seconds=3600,
        empty_ttl_seconds=15,
        lock_ttl_seconds=60,
        wait_timeout_seconds=0,
    )

    def follower_compute() -> dict:
        nonlocal follower_compute_count
        follower_compute_count += 1
        return {"requirements": ["duplicate"]}

    with pytest.raises(RedisSingleFlightBusy):
        redis_singleflight(
            redis_client,
            key="requirements:publish-failure",
            compute=follower_compute,
            deserialize=json.loads,
            serialize=json.dumps,
            cacheable=lambda value: bool(value["requirements"]),
            success_ttl_seconds=3600,
            empty_ttl_seconds=15,
            lock_ttl_seconds=60,
            wait_timeout_seconds=0,
        )

    assert result == {"requirements": ["Python"]}
    assert follower_compute_count == 0
    assert redis_client.get("requirements:publish-failure:singleflight") is not None
