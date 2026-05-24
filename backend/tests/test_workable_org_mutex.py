"""Per-org Workable mutex: self-healing lock + interactive-write preemption.

Covers the two 2026-05-24 production failure modes:

1. A worker SIGKILLed mid-hold used to leak the lock for the full 30-min
   TTL, timing out every Workable write. The lock now carries a unique
   token refreshed by a heartbeat, with a short TTL — so a dead holder's
   lock self-heals fast and we never extend/delete a lock we don't own.
2. A read-heavy sync held the global write-mutex for its whole run (~1h),
   starving decision approvals. A blocked interactive write now flags a
   waiter key; the holding sync yields the lock at its next checkpoint.
"""
from __future__ import annotations

import sys
import threading
import time

import fakeredis
import pytest

from app.tasks import assessment_tasks as at


@pytest.fixture()
def fake_redis(monkeypatch):
    """Point the mutex helpers at a shared in-memory Redis.

    The helpers build a fresh client per call (``redis.Redis.from_url``);
    a shared ``FakeServer`` makes those clients see one keyspace.
    """
    server = fakeredis.FakeServer()

    class _FakeRedisModule:
        Redis = type(
            "_RedisStub",
            (),
            {"from_url": staticmethod(lambda url: fakeredis.FakeRedis(server=server))},
        )

    monkeypatch.setitem(sys.modules, "redis", _FakeRedisModule)
    return fakeredis.FakeRedis(server=server)


# --------------------------------------------------------------------------
# Ownership-safe primitives (the bits that make the short TTL safe).
# --------------------------------------------------------------------------


def test_compare_and_delete_only_deletes_own_token(fake_redis):
    fake_redis.set("k", "mine")
    at._compare_and_delete(fake_redis, "k", "someone_else")
    assert fake_redis.get("k") == b"mine", "must not delete a lock we don't own"
    at._compare_and_delete(fake_redis, "k", "mine")
    assert fake_redis.get("k") is None, "deletes our own lock"


def test_compare_and_extend_only_extends_own_token(fake_redis):
    fake_redis.set("k", "mine", ex=100)
    at._compare_and_extend(fake_redis, "k", "stale", 9999)
    assert fake_redis.ttl("k") <= 100, "must not extend a lock we don't own"
    at._compare_and_extend(fake_redis, "k", "mine", 9999)
    assert fake_redis.ttl("k") > 100, "extends our own lock"


# --------------------------------------------------------------------------
# Acquire / release / contention.
# --------------------------------------------------------------------------


def test_acquire_release_and_contention(fake_redis):
    held = at._acquire_workable_org_mutex(7, source="agent")
    assert isinstance(held, at._OrgMutex)
    # Value is a unique token prefixed with the source, not a bare label.
    assert fake_redis.get(held.key) == held.token.encode()
    assert held.token.startswith("agent:")

    assert at._acquire_workable_org_mutex(7, source="starred") is None
    # Different org is independent.
    other = at._acquire_workable_org_mutex(8, source="starred")
    assert isinstance(other, at._OrgMutex)

    at._release_workable_org_mutex(held)
    assert fake_redis.get(held.key) is None
    after = at._acquire_workable_org_mutex(7, source="jobs")
    assert isinstance(after, at._OrgMutex)

    at._release_workable_org_mutex(other)
    at._release_workable_org_mutex(after)


def test_release_tolerates_non_handles():
    # None (held by another) and False (redis down) must be safe no-ops.
    at._release_workable_org_mutex(None)
    at._release_workable_org_mutex(False)


def test_acquire_returns_false_when_redis_unavailable(monkeypatch):
    class _BoomModule:
        Redis = type(
            "_Boom", (), {"from_url": staticmethod(lambda url: (_ for _ in ()).throw(RuntimeError("down")))}
        )

    monkeypatch.setitem(sys.modules, "redis", _BoomModule)
    assert at._acquire_workable_org_mutex(1, source="agent") is False


# --------------------------------------------------------------------------
# Waiter signaling (interactive-write priority).
# --------------------------------------------------------------------------


def test_blocked_interactive_write_sets_waiter_flag(fake_redis):
    held = at._acquire_workable_org_mutex(3, source="agent")
    assert isinstance(held, at._OrgMutex)
    waiter_k = at._waiter_key(held.key)

    # A sync that's merely blocked does NOT flag a waiter.
    assert at._acquire_workable_org_mutex(3, source="starred") is None
    assert not fake_redis.exists(waiter_k)

    # An interactive op that's blocked DOES flag a waiter.
    assert (
        at._acquire_workable_org_mutex(3, source="workable_op:approve", signal_waiter=True)
        is None
    )
    assert fake_redis.exists(waiter_k)

    at._release_workable_org_mutex(held)


def test_successful_acquire_clears_stale_waiter_flag(fake_redis):
    key = f"{at._WORKABLE_ORG_MUTEX_KEY_PREFIX}:5"
    fake_redis.set(at._waiter_key(key), "leftover", ex=60)
    held = at._acquire_workable_org_mutex(5, source="agent")
    assert isinstance(held, at._OrgMutex)
    assert not fake_redis.exists(at._waiter_key(key)), "acquiring clears the waiter flag"
    at._release_workable_org_mutex(held)


# --------------------------------------------------------------------------
# Cooperative yield: a holding sync hands off to a waiting interactive op.
# --------------------------------------------------------------------------


def test_yield_is_noop_without_waiter(fake_redis):
    held = at._acquire_workable_org_mutex(11, source="agent")
    token_before = held.token
    at._yield_workable_org_mutex_if_waiter(held, 11)
    assert held.token == token_before, "no waiter → keep holding, same token"
    assert fake_redis.get(held.key) == held.token.encode()
    at._release_workable_org_mutex(held)


def test_yield_hands_off_to_op_then_reacquires(fake_redis, monkeypatch):
    monkeypatch.setattr(at, "_WORKABLE_ORG_MUTEX_YIELD_POLL_SECONDS", 0.02)

    held = at._acquire_workable_org_mutex(99, source="agent")
    assert isinstance(held, at._OrgMutex)
    sync_token = held.token

    op_ran = threading.Event()
    op_done = threading.Event()

    def _op():
        # Mirror the op shell's retry loop: keep trying (flagging a waiter)
        # until the sync yields, then hold briefly and release.
        deadline = time.monotonic() + 10
        op_lock = None
        while time.monotonic() < deadline:
            op_lock = at._acquire_workable_org_mutex(
                99, source="workable_op:approve", signal_waiter=True
            )
            if isinstance(op_lock, at._OrgMutex):
                break
            time.sleep(0.02)
        assert isinstance(op_lock, at._OrgMutex), "op should win the lock once sync yields"
        op_ran.set()
        time.sleep(0.1)
        at._release_workable_org_mutex(op_lock)
        op_done.set()

    t = threading.Thread(target=_op)
    t.start()
    try:
        # The op thread has flagged a waiter; sync yields, lets the op run,
        # then takes the lock back with a fresh token.
        at._yield_workable_org_mutex_if_waiter(held, 99)
    finally:
        t.join(timeout=10)

    assert op_ran.is_set(), "interactive op got its turn"
    assert op_done.is_set()
    assert held.token != sync_token, "sync re-acquired with a new token"
    assert fake_redis.get(held.key) == held.token.encode(), "sync holds the lock again"

    at._release_workable_org_mutex(held)
