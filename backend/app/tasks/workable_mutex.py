from __future__ import annotations

import logging
import uuid

from ..platform.config import settings

logger = logging.getLogger("app.tasks.assessment_tasks")

# Note: ``sync_workable_orgs`` (every-30-min full sync of every job AND
# every candidate AND every CV download) was removed on 2026-05-20. It
# was the source of the constant rate-limiting and the starvation bug
# (``workable_last_sync_at`` debounce starved by ``sync_starred_roles``
# 's writes — see PR #194). Sync is now split per-cadence: jobs every
# 15 min (jobs_only), starred-role candidates every 5 min, agent-mode
# candidates every 5 min, everything else once nightly.

# Single per-org mutex shared by all four Workable sync tasks. Two tasks
# touching the same Workable token at the same time used to share-rate-
# limit each other into 429s (each ``sync_org`` calls ``list_open_jobs``
# which fires 5 endpoint hits, and per-candidate prefetches fan out
# further). A single mutex means only one task type is talking to
# Workable for a given org at a time. If a task can't get the lock it
# skips that fire — the next Beat tick (5-15 min away) will retry.
_WORKABLE_ORG_MUTEX_KEY_PREFIX = "celery:lock:workable_org_sync"
# Fallback TTL for a mutex acquired WITHOUT a heartbeat. No live caller uses
# this path: every sync task and the op path now acquire with ``heartbeat=True``
# (short TTL + renew-while-alive) so a worker SIGKILLed mid-run frees the lock
# in ~2 min instead of leaking it for the full TTL and blocking every Workable
# write for the org until then. Do NOT acquire the Workable mutex without a
# heartbeat.
_WORKABLE_ORG_MUTEX_TTL_SECONDS = 1800

# The op path (``run_workable_op_task``) AND all four sync tasks acquire with
# this SHORT TTL plus a heartbeat thread that re-extends it while the holder is
# alive. A worker killed mid-run (deploy SIGKILL) takes the heartbeat thread
# down with it, so the lock auto-expires in ~2 min instead of leaking for the
# full static TTL above and blocking every Workable write for the org until
# then. The interval is a third of the TTL so a single missed beat never
# expires a live lock.
_WORKABLE_OP_MUTEX_TTL_SECONDS = 120
_WORKABLE_OP_MUTEX_HEARTBEAT_SECONDS = 40


_EXTEND_MUTEX_IF_OWNED_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_DELETE_MUTEX_IF_OWNED_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_CHECK_MUTEX_IF_OWNED_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return 1
end
return 0
"""


def _extend_workable_mutex_if_owned(
    client, key: str, owner_token: str, ttl_seconds: int
) -> bool:
    """Atomically renew ``key`` only while ``owner_token`` still owns it."""
    return bool(
        client.eval(
            _EXTEND_MUTEX_IF_OWNED_LUA,
            1,
            key,
            owner_token,
            int(ttl_seconds),
        )
    )


def _delete_workable_mutex_if_owned(client, key: str, owner_token: str) -> bool:
    """Atomically delete ``key`` only while ``owner_token`` still owns it."""
    return bool(client.eval(_DELETE_MUTEX_IF_OWNED_LUA, 1, key, owner_token))


def _workable_mutex_heartbeat(
    client,
    key: str,
    ttl_seconds: int,
    stop_event,
    owner_token: str,
    ownership_lost_event=None,
) -> None:
    """Re-extend the mutex TTL every interval until released (or the process
    dies), but only while this acquisition still owns the Redis key.

    A transient Redis failure is retried on the next heartbeat, while the
    observer event tells provider loops to stop at their next safe boundary.
    If another task acquired the key, the compare-and-expire script returns
    false and this stale heartbeat exits without extending the new lease.
    """
    interval = max(1, min(_WORKABLE_OP_MUTEX_HEARTBEAT_SECONDS, ttl_seconds // 3))
    while not stop_event.wait(interval):
        try:
            if not _extend_workable_mutex_if_owned(
                client, key, owner_token, ttl_seconds
            ):
                if ownership_lost_event is not None:
                    ownership_lost_event.set()
                return
        except Exception:
            if ownership_lost_event is not None:
                # We cannot prove the lease survived. Fail provider loops
                # closed, but keep retrying so the heartbeat itself tolerates
                # a one-off transport failure and never touches a replacement.
                ownership_lost_event.set()
            logger.exception("workable mutex heartbeat failed key=%s", key)


def _workable_mutex_ownership_lost(handle) -> bool:
    """Whether a heartbeat could no longer prove this handle owns its lease."""
    if not handle:
        return True
    try:
        event = handle[4]
    except (IndexError, KeyError, TypeError):
        # Legacy handles did not carry the observer event.  They retain their
        # former semantics; every live heartbeat acquisition now returns it.
        event = None
    return bool(event is not None and event.is_set())


def _workable_mutex_is_owned(handle) -> bool:
    """Atomically prove that this exact handle still owns its Redis lease.

    The heartbeat observer is intentionally only an early warning: a lease can
    expire and be acquired by a replacement before the heartbeat thread sets
    its event. Terminal database writes therefore use this exact token compare
    at their final safe boundary. Unknown Redis state fails closed.
    """
    if _workable_mutex_ownership_lost(handle):
        return False
    try:
        client, key, owner_token = handle[0], handle[1], handle[3]
        ownership_lost_event = handle[4] if len(handle) > 4 else None
    except (IndexError, KeyError, TypeError):
        return False
    if not key or not owner_token:
        return False
    try:
        return bool(
            client.eval(
                _CHECK_MUTEX_IF_OWNED_LUA,
                1,
                key,
                owner_token,
            )
        )
    except Exception:
        if ownership_lost_event is not None:
            ownership_lost_event.set()
        logger.exception("workable mutex ownership check failed key=%s", key)
        return False


def _acquire_workable_org_mutex(
    org_id: int,
    *,
    source: str,
    ttl: int | None = None,
    heartbeat: bool = False,
    namespace: str = _WORKABLE_ORG_MUTEX_KEY_PREFIX,
):
    """Acquire the per-org Workable mutex shared across all sync tasks + ops.

    ``source`` is a short label (``"jobs"`` / ``"starred"`` / ``"agent"`` /
    ``"nightly"`` / ``"workable_op:<op>"``) included in a unique ownership
    token, so Redis remains debuggable without allowing an expired holder to
    renew or release a replacement holder's lock.

    ``heartbeat=True`` (all live sync/write paths) acquires with the short op
    TTL and spawns a daemon thread that renews it while the holder lives. The
    static 30-minute TTL remains only as a defensive non-heartbeat fallback.

    ``namespace`` is the Redis key prefix; it defaults to the Workable lock so
    every existing caller is unchanged. The Bullhorn sync passes its own
    namespace so Bullhorn and Workable syncs for the same org don't contend on
    one lock (they talk to different APIs with independent rate budgets).

    Returns an opaque handle on success, ``None`` if held by another task, and
    ``False`` on Redis failure. Provider callers must defer/retry for both
    failure results; running unguarded would reintroduce concurrent ATS calls.
    """
    ttl_seconds = int(
        ttl
        if ttl is not None
        else (
            _WORKABLE_OP_MUTEX_TTL_SECONDS
            if heartbeat
            else _WORKABLE_ORG_MUTEX_TTL_SECONDS
        )
    )
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{namespace}:{org_id}"
        owner_token = f"{source}:{uuid.uuid4().hex}"
        if not client.set(key, owner_token, nx=True, ex=ttl_seconds):
            return None
        stop_event = None
        if heartbeat:
            import threading

            stop_event = threading.Event()
            ownership_lost_event = threading.Event()
            threading.Thread(
                target=_workable_mutex_heartbeat,
                args=(
                    client,
                    key,
                    ttl_seconds,
                    stop_event,
                    owner_token,
                    ownership_lost_event,
                ),
                name=f"workable-mutex-hb:{org_id}",
                daemon=True,
            ).start()
        else:
            ownership_lost_event = None
        # Keep the existing tuple positions stable; the ownership token is
        # appended so callers can continue treating the handle as opaque.
        return (client, key, stop_event, owner_token, ownership_lost_event)
    except Exception:
        logger.exception(
            "Failed to acquire workable-org mutex org_id=%s source=%s; deferring provider call",
            org_id,
            source,
        )
        return False


def _release_workable_org_mutex(handle) -> None:
    if not handle:
        return
    try:
        client, key = handle[0], handle[1]
        stop_event = handle[2] if len(handle) > 2 else None
        if stop_event is not None:
            stop_event.set()  # stop the heartbeat before freeing the key
        owner_token = handle[3] if len(handle) > 3 else None
        if not owner_token:
            # A legacy/incomplete handle cannot prove ownership. Let the TTL
            # expire instead of risking deletion of a replacement holder.
            logger.error("Refusing to release workable-org mutex without owner token")
            return
        _delete_workable_mutex_if_owned(client, key, owner_token)
    except Exception:
        logger.exception("Failed to release workable-org mutex")


# ---------------------------------------------------------------------------
# Op-priority signal. User-facing Workable writes (decision approvals /
# overrides) are tiny and latency-sensitive, but they share the per-org mutex
# with the periodic candidate syncs — which hold it for tens of minutes while
# walking a rate-limited candidate list. With no fairness, a steady drip of
# 5-min syncs starves an approve batch until it times out ("Workable lock
# timeout"). This flag lets a pending op tell the syncs to stand aside: it's
# set when an op is enqueued and refreshed while the op waits for the lock; the
# sync tasks skip an org whose flag is set, and an in-flight ``sync_org`` yields
# the lock at the next job boundary. Short TTL so it self-clears once the op
# finishes (no explicit clear) — the mutex still guarantees correctness if the
# flag is ever missed.
_WORKABLE_OP_PENDING_KEY_PREFIX = "celery:lock:workable_op_pending"
_WORKABLE_OP_PENDING_TTL_SECONDS = 120


def mark_workable_op_pending(org_id: int) -> None:
    """Signal that a user-facing Workable write is queued/waiting for this org
    so the periodic syncs yield the per-org mutex. Best-effort; never raises."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.set(
            f"{_WORKABLE_OP_PENDING_KEY_PREFIX}:{org_id}",
            "1",
            ex=_WORKABLE_OP_PENDING_TTL_SECONDS,
        )
    except Exception:
        logger.exception("mark_workable_op_pending failed org_id=%s", org_id)


def is_workable_op_pending(org_id: int) -> bool:
    """True if a user-facing Workable write is pending for this org. Fail-open
    (returns False on Redis error) — a flaky signal must never wedge syncs; the
    mutex still serializes writes whenever Redis is up."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        return bool(client.exists(f"{_WORKABLE_OP_PENDING_KEY_PREFIX}:{org_id}"))
    except Exception:
        logger.exception("is_workable_op_pending failed org_id=%s", org_id)
        return False
