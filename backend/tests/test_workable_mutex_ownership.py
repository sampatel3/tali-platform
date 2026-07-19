from __future__ import annotations

from threading import Event
from unittest.mock import patch

import pytest

from app.tasks import workable_mutex
from app.tasks import workable_sync_serialization as manual_sync
from app.components.integrations.workable.sync_lease import WorkableSyncYielded
from app.components.integrations.workable.sync_provider_reads import (
    prefetch_candidate_resumes,
    prefetch_full_candidate_payloads,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.eval_calls = 0
        self.eval_failures = 0

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[str(key)] = str(value)
        if ex is not None:
            self.ttls[str(key)] = int(ex)
        return True

    def eval(self, script, _num_keys, key, owner_token, *args):
        self.eval_calls += 1
        if self.eval_failures:
            self.eval_failures -= 1
            raise ConnectionError("temporary Redis outage")
        key = str(key)
        if self.store.get(key) != owner_token:
            return 0
        if script == workable_mutex._EXTEND_MUTEX_IF_OWNED_LUA:
            self.ttls[key] = int(args[0])
            return 1
        if script == workable_mutex._DELETE_MUTEX_IF_OWNED_LUA:
            self.store.pop(key, None)
            self.ttls.pop(key, None)
            return 1
        raise AssertionError("unexpected script")


class _SequencedStop:
    def __init__(self, values: list[bool]) -> None:
        self._values = iter(values)
        self.wait_calls = 0

    def wait(self, _interval: int) -> bool:
        self.wait_calls += 1
        return next(self._values)


def test_expired_holder_cannot_extend_or_delete_replacement() -> None:
    client = _FakeRedis()
    with patch("redis.Redis.from_url", return_value=client):
        stale = workable_mutex._acquire_workable_org_mutex(17, source="jobs")
    assert stale not in (None, False)
    key, stale_owner = stale[1], stale[3]
    assert stale_owner.startswith("jobs:")

    # Model TTL expiry followed by a different task acquiring the same key.
    client.store.pop(key)
    with patch("redis.Redis.from_url", return_value=client):
        replacement = workable_mutex._acquire_workable_org_mutex(
            17, source="starred"
        )
    assert replacement not in (None, False)
    replacement_owner = replacement[3]
    assert replacement_owner.startswith("starred:")
    assert replacement_owner != stale_owner

    assert (
        workable_mutex._extend_workable_mutex_if_owned(
            client, key, stale_owner, ttl_seconds=999
        )
        is False
    )
    workable_mutex._release_workable_org_mutex(stale)
    assert client.store[key] == replacement_owner
    assert client.ttls[key] != 999

    workable_mutex._release_workable_org_mutex(replacement)
    assert key not in client.store


def test_heartbeat_retries_after_one_transient_redis_failure() -> None:
    client = _FakeRedis()
    client.store["mutex"] = "jobs:owner"
    client.eval_failures = 1
    stop = _SequencedStop([False, False, True])
    ownership_lost = Event()

    workable_mutex._workable_mutex_heartbeat(
        client,
        "mutex",
        ttl_seconds=12,
        stop_event=stop,
        owner_token="jobs:owner",
        ownership_lost_event=ownership_lost,
    )

    assert client.eval_calls == 2
    assert client.ttls["mutex"] == 12
    assert stop.wait_calls == 3
    assert ownership_lost.is_set()


def test_heartbeat_stops_when_ownership_has_changed() -> None:
    client = _FakeRedis()
    client.store["mutex"] = "starred:replacement"
    stop = _SequencedStop([False])
    ownership_lost = Event()

    workable_mutex._workable_mutex_heartbeat(
        client,
        "mutex",
        ttl_seconds=12,
        stop_event=stop,
        owner_token="jobs:stale",
        ownership_lost_event=ownership_lost,
    )

    assert client.eval_calls == 1
    assert client.store["mutex"] == "starred:replacement"
    assert stop.wait_calls == 1
    assert ownership_lost.is_set()


def test_ownership_helper_fails_closed_for_missing_or_signalled_handle() -> None:
    lost = Event()
    handle = (object(), "mutex", Event(), "jobs:owner", lost)

    assert workable_mutex._workable_mutex_ownership_lost(False) is True
    assert workable_mutex._workable_mutex_ownership_lost(handle) is False
    lost.set()
    assert workable_mutex._workable_mutex_ownership_lost(handle) is True


def test_acquire_returns_false_when_redis_is_unavailable() -> None:
    with patch("redis.Redis.from_url", side_effect=ConnectionError("offline")):
        handle = workable_mutex._acquire_workable_org_mutex(23, source="jobs")

    assert handle is False


@pytest.mark.parametrize("resume_wave", [False, True])
def test_prefetch_wave_stops_queued_requests_after_lease_loss(resume_wave) -> None:
    """Only already-running workers may finish after the lease is lost."""

    lost = Event()
    calls: list[str] = []

    class _Client:
        def get_candidate(self, candidate_id: str) -> dict:
            calls.append(candidate_id)
            lost.set()
            return {"id": candidate_id}

        def download_candidate_resume(self, payload: dict):
            candidate_id = str(payload["id"])
            calls.append(candidate_id)
            lost.set()
            return f"{candidate_id}.pdf", b"pdf"

    candidates = [{"id": str(index)} for index in range(20)]
    with pytest.raises(WorkableSyncYielded):
        if resume_wave:
            prefetch_candidate_resumes(
                _Client(),
                {str(item["id"]): item for item in candidates},
                should_yield=lost.is_set,
            )
        else:
            prefetch_full_candidate_payloads(
                _Client(),
                candidates,
                is_terminal=lambda _item: False,
                should_yield=lost.is_set,
            )

    assert 1 <= len(calls) <= 3


def test_workable_pagination_stops_before_next_request_after_lease_loss(
    monkeypatch,
) -> None:
    from app.components.integrations.workable.service import WorkableService

    client = WorkableService(access_token="token", subdomain="example")
    lost = Event()
    first_page = {
        "candidates": [{"id": "one"}],
        "paging": {"next": "https://example.workable.com/spi/v3/jobs/J1/candidates?page=2"},
    }

    def _first_request(*_args, **_kwargs):
        lost.set()
        return first_page

    monkeypatch.setattr(client, "_request", _first_request)
    monkeypatch.setattr(
        "app.components.integrations.workable.service.httpx.Client",
        lambda **_kwargs: pytest.fail("next page request must not start"),
    )
    previous = getattr(client, "_sync_lease_observer", None)
    client._sync_lease_observer = lost.is_set
    try:
        with pytest.raises(WorkableSyncYielded):
            client.list_job_candidates("J1", paginate=True, max_pages=None)
    finally:
        client._sync_lease_observer = previous


class _RetryScheduled(RuntimeError):
    def __init__(self, countdown: int) -> None:
        self.countdown = countdown
        super().__init__(f"retry in {countdown}")


class _FakeTask:
    def __init__(self, retries: int = 0) -> None:
        self.request = type("_Request", (), {"retries": retries})()

    def retry(self, *, countdown: int):
        raise _RetryScheduled(countdown)


@pytest.mark.parametrize("acquire_result", [None, False])
def test_manual_sync_never_calls_provider_when_mutex_is_busy_or_unavailable(
    monkeypatch, acquire_result
) -> None:
    monkeypatch.setattr(manual_sync, "_run_is_active", lambda **_kwargs: True)
    monkeypatch.setattr(
        manual_sync, "_acquire_workable_org_mutex", lambda *_a, **_k: acquire_result
    )
    provider_calls = 0

    def _provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1

    monkeypatch.setattr(manual_sync, "execute_workable_sync_run", _provider)

    with pytest.raises(_RetryScheduled) as raised:
        manual_sync.execute_serialized_workable_sync(
            _FakeTask(),
            org_id=31,
            run_id=41,
            mode="metadata",
            selected_job_shortcodes=None,
        )

    assert raised.value.countdown == 5
    assert provider_calls == 0


def test_manual_sync_holds_owned_mutex_across_provider_call(monkeypatch) -> None:
    handle = (object(), "mutex", Event(), "manual_sync:owner", Event())
    order: list[str] = []

    def _acquire(org_id, *, source, heartbeat):
        assert (org_id, source, heartbeat) == (31, "manual_sync:41", True)
        order.append("acquire")
        return handle

    monkeypatch.setattr(manual_sync, "_run_is_active", lambda **_kwargs: True)
    monkeypatch.setattr(manual_sync, "_acquire_workable_org_mutex", _acquire)
    def _provider(**kwargs):
        assert kwargs["should_yield"]() is False
        order.append("provider")

    monkeypatch.setattr(manual_sync, "execute_workable_sync_run", _provider)
    monkeypatch.setattr(
        manual_sync,
        "_release_workable_org_mutex",
        lambda released: order.append("release") if released is handle else None,
    )

    manual_sync.execute_serialized_workable_sync(
        _FakeTask(),
        org_id=31,
        run_id=41,
        mode="metadata",
        selected_job_shortcodes=["JOB1"],
    )

    assert order == ["acquire", "provider", "release"]


def test_manual_sync_retries_without_provider_when_acquired_lease_is_lost(
    monkeypatch,
) -> None:
    lost = Event()
    lost.set()
    handle = (object(), "mutex", Event(), "manual_sync:owner", lost)
    provider_calls = 0
    released: list[object] = []
    monkeypatch.setattr(manual_sync, "_run_is_active", lambda **_kwargs: True)
    monkeypatch.setattr(
        manual_sync, "_acquire_workable_org_mutex", lambda *_a, **_k: handle
    )

    def _provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1

    monkeypatch.setattr(manual_sync, "execute_workable_sync_run", _provider)
    monkeypatch.setattr(manual_sync, "_release_workable_org_mutex", released.append)

    with pytest.raises(_RetryScheduled):
        manual_sync.execute_serialized_workable_sync(
            _FakeTask(),
            org_id=31,
            run_id=41,
            mode="metadata",
            selected_job_shortcodes=None,
        )

    assert provider_calls == 0
    assert released == [handle]


def test_manual_sync_rechecks_durable_run_after_acquiring_mutex(monkeypatch) -> None:
    active = iter([True, False])
    handle = (object(), "mutex", Event(), "manual_sync:owner", Event())
    provider_calls = 0
    released: list[object] = []
    monkeypatch.setattr(
        manual_sync, "_run_is_active", lambda **_kwargs: next(active)
    )
    monkeypatch.setattr(
        manual_sync, "_acquire_workable_org_mutex", lambda *_a, **_k: handle
    )

    def _provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1

    monkeypatch.setattr(manual_sync, "execute_workable_sync_run", _provider)
    monkeypatch.setattr(
        manual_sync, "_release_workable_org_mutex", released.append
    )

    manual_sync.execute_serialized_workable_sync(
        _FakeTask(),
        org_id=31,
        run_id=41,
        mode="metadata",
        selected_job_shortcodes=None,
    )

    assert provider_calls == 0
    assert released == [handle]
