from __future__ import annotations

import logging

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


def _workable_mutex_heartbeat(client, key: str, ttl_seconds: int, stop_event) -> None:
    """Re-extend the mutex TTL every interval until released (or the process
    dies). ``expire`` only touches an existing key, so a beat racing with
    ``delete`` in release can never resurrect a freed lock."""
    interval = max(1, min(_WORKABLE_OP_MUTEX_HEARTBEAT_SECONDS, ttl_seconds // 3))
    while not stop_event.wait(interval):
        try:
            client.expire(key, ttl_seconds)
        except Exception:
            logger.exception("workable mutex heartbeat failed key=%s", key)
            return


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
    ``"nightly"`` / ``"workable_op:<op>"``) recorded as the lock value so we
    can see in Redis which task is holding the lock when debugging.

    ``heartbeat=True`` (op path) acquires with the short op TTL and spawns a
    daemon thread that renews it while the holder lives — deploy-safe. Sync
    callers leave it off and get the static 30-min TTL.

    ``namespace`` is the Redis key prefix; it defaults to the Workable lock so
    every existing caller is unchanged. The Bullhorn sync passes its own
    namespace so Bullhorn and Workable syncs for the same org don't contend on
    one lock (they talk to different APIs with independent rate budgets).

    Returns the handle on success, ``None`` if held by another task,
    ``False`` on Redis failure (caller treats as "run unguarded").
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
        if not client.set(key, source, nx=True, ex=ttl_seconds):
            return None
        stop_event = None
        if heartbeat:
            import threading

            stop_event = threading.Event()
            threading.Thread(
                target=_workable_mutex_heartbeat,
                args=(client, key, ttl_seconds, stop_event),
                name=f"workable-mutex-hb:{org_id}",
                daemon=True,
            ).start()
        return (client, key, stop_event)
    except Exception:
        logger.exception(
            "Failed to acquire workable-org mutex org_id=%s source=%s; running unguarded",
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
        client.delete(key)
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
