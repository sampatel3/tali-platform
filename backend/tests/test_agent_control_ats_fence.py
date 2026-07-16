from __future__ import annotations

from threading import Event, Lock, Thread

import pytest
from fastapi import HTTPException

from app.services import agent_control_ats_fence as fence


def _install_fake_mutex(monkeypatch):
    held: set[tuple[str, int]] = set()
    guard = Lock()

    def _acquire(org_id, *, source, heartbeat, namespace):
        assert source == "agent_control"
        assert heartbeat is True
        key = (str(namespace), int(org_id))
        with guard:
            if key in held:
                return None
            held.add(key)
        return (key,)

    def _release(handle):
        with guard:
            held.discard(handle[0])

    monkeypatch.setattr(fence, "_namespaces", lambda: ("bullhorn", "workable"))
    monkeypatch.setattr(
        "app.tasks.workable_mutex._acquire_workable_org_mutex", _acquire
    )
    monkeypatch.setattr(
        "app.tasks.workable_mutex._release_workable_org_mutex", _release
    )
    return held, guard


def test_control_fence_waits_for_provider_mutex_then_acquires(monkeypatch):
    held, guard = _install_fake_mutex(monkeypatch)
    provider_handles = fence.acquire_agent_control_ats_fence(17, wait_seconds=0)
    acquired = Event()
    control_handles: list[object] = []

    def _control() -> None:
        control_handles.extend(
            fence.acquire_agent_control_ats_fence(17, wait_seconds=1)
        )
        acquired.set()

    thread = Thread(target=_control)
    thread.start()
    assert acquired.wait(timeout=0.1) is False
    fence._release(provider_handles)
    thread.join(timeout=1)

    assert acquired.is_set()
    with guard:
        assert held == {("bullhorn", 17), ("workable", 17)}
    fence._release(control_handles)
    assert held == set()


def test_control_fence_fails_closed_when_busy(monkeypatch):
    _held, _guard = _install_fake_mutex(monkeypatch)
    provider_handles = fence.acquire_agent_control_ats_fence(23, wait_seconds=0)
    try:
        with pytest.raises(fence.AgentControlAtsFenceUnavailable) as raised:
            fence.acquire_agent_control_ats_fence(23, wait_seconds=0)
    finally:
        fence._release(provider_handles)

    assert raised.value.busy is True


def test_control_fence_fails_closed_when_mutex_backend_is_unavailable(monkeypatch):
    monkeypatch.setattr(fence, "_namespaces", lambda: ("workable",))
    monkeypatch.setattr(
        "app.tasks.workable_mutex._acquire_workable_org_mutex",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(fence.AgentControlAtsFenceUnavailable) as raised:
        fence.acquire_agent_control_ats_fence(29, wait_seconds=0)

    assert raised.value.busy is False


def test_transaction_fence_is_held_until_commit(db, monkeypatch):
    held, _guard = _install_fake_mutex(monkeypatch)

    fence.require_agent_control_transaction_fence(db, organization_id=31)
    assert held == {("bullhorn", 31), ("workable", 31)}

    db.commit()
    assert held == set()


@pytest.mark.parametrize(
    ("busy", "status_code"),
    ((True, 409), (False, 503)),
)
def test_transaction_fence_returns_visible_http_failure(
    db, monkeypatch, *, busy: bool, status_code: int
):
    def _fail(_organization_id):
        raise fence.AgentControlAtsFenceUnavailable(busy=busy)

    monkeypatch.setattr(fence, "acquire_agent_control_ats_fence", _fail)

    with pytest.raises(HTTPException) as raised:
        fence.require_agent_control_transaction_fence(db, organization_id=37)

    assert raised.value.status_code == status_code
    assert "No agent control changed" in str(raised.value.detail)
