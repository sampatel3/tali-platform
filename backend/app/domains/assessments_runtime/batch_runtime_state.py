"""Shared in-memory and Redis state for application batch jobs."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from ...platform.config import settings

logger = logging.getLogger("taali.application_batches")

batch_score_progress: dict[int, dict] = {}
batch_fetch_cvs_progress: dict[int, dict] = {}
batch_pre_screen_progress: dict[int, dict] = {}
sync_graph_progress: dict[int, dict] = {}

BATCH_SCORE_CANCEL_PREFIX = "batch_score:cancel:"
BATCH_FETCH_CANCEL_PREFIX = "batch_fetch_cvs:cancel:"
BATCH_META_PREFIX = "batch_score:meta:"
BATCH_QUEUE_PREFIX = "batch_score:queued:"
CANCEL_FLAG_TTL_SECONDS = 3600
BATCH_META_TTL_SECONDS = 7200
BATCH_QUEUE_TTL_SECONDS = 7200


def redis_client():
    """Return a lazy Redis client, or ``None`` when Redis is unavailable."""

    try:
        import redis  # type: ignore

        return redis.Redis.from_url(settings.REDIS_URL)
    except Exception:
        logger.exception("Failed to build redis client for batch state")
        return None


def set_cancel_flag(prefix: str, role_id: int) -> bool:
    client = redis_client()
    if client is None:
        return False
    try:
        client.set(f"{prefix}{role_id}", "1", ex=CANCEL_FLAG_TTL_SECONDS)
        return True
    except Exception:
        logger.exception("Failed to set cancel flag for role_id=%s", role_id)
        return False


def is_cancelled(prefix: str, role_id: int) -> bool:
    client = redis_client()
    if client is None:
        return False
    try:
        return bool(client.get(f"{prefix}{role_id}"))
    except Exception:
        return False


def clear_cancel_flag(prefix: str, role_id: int) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.delete(f"{prefix}{role_id}")
    except Exception:
        pass


def write_batch_meta(
    role_id: int,
    *,
    total: int,
    started_at: datetime,
    include_scored: bool,
    related_evaluation_ids: list[int] | None = None,
) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.set(
            f"{BATCH_META_PREFIX}{role_id}",
            json.dumps(
                {
                    "total": total,
                    "started_at": started_at.isoformat(),
                    "include_scored": bool(include_scored),
                    "related_evaluation_ids": related_evaluation_ids,
                }
            ),
            ex=BATCH_META_TTL_SECONDS,
        )
    except Exception:
        pass


def read_batch_meta(role_id: int) -> dict | None:
    client = redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{BATCH_META_PREFIX}{role_id}")
        return json.loads(raw) if raw is not None else None
    except Exception:
        return None


def delete_batch_meta(role_id: int) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.delete(f"{BATCH_META_PREFIX}{role_id}")
    except Exception:
        pass


def write_batch_queue(
    role_id: int, *, include_scored: bool, applied_after: str | None
) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.set(
            f"{BATCH_QUEUE_PREFIX}{role_id}",
            json.dumps(
                {
                    "include_scored": bool(include_scored),
                    "applied_after": applied_after,
                }
            ),
            ex=BATCH_QUEUE_TTL_SECONDS,
        )
    except Exception:
        pass


def read_batch_queue(role_id: int) -> dict | None:
    client = redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{BATCH_QUEUE_PREFIX}{role_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def clear_batch_queue(role_id: int) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.delete(f"{BATCH_QUEUE_PREFIX}{role_id}")
    except Exception:
        pass


def is_batch_score_cancelled(role_id: int) -> bool:
    return is_cancelled(BATCH_SCORE_CANCEL_PREFIX, role_id)


def is_batch_fetch_cancelled(role_id: int) -> bool:
    return is_cancelled(BATCH_FETCH_CANCEL_PREFIX, role_id)
