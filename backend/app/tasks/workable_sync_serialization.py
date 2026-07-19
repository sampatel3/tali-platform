"""Fail-closed provider mutex shell for user-requested Workable sync runs."""

from __future__ import annotations

import logging

from ..components.integrations.workable.sync_runner import execute_workable_sync_run
from .workable_mutex import (
    _acquire_workable_org_mutex,
    _release_workable_org_mutex,
    _workable_mutex_ownership_lost,
)

logger = logging.getLogger(__name__)


def _retry_countdown(retries: int) -> int:
    """Poll quickly once, then bound mutex retry traffic to once per minute."""
    return min(60, 5 * (2 ** min(max(0, int(retries)), 4)))


def _run_is_active(*, org_id: int, run_id: int) -> bool:
    """Read-only idempotency gate for duplicate/stale Celery deliveries."""
    from ..models.workable_sync_run import WorkableSyncRun
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        run = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.id == int(run_id),
                WorkableSyncRun.organization_id == int(org_id),
            )
            .one_or_none()
        )
        return bool(
            run is not None
            and run.status == "running"
            and run.finished_at is None
        )
    finally:
        db.close()


def execute_serialized_workable_sync(
    task,
    *,
    org_id: int,
    run_id: int,
    mode: str,
    selected_job_shortcodes: list[str] | None,
) -> None:
    """Run a durable manual sync only while its shared provider mutex is owned.

    The Celery task has an unlimited retry count with bounded backoff, so an
    unavailable mutex never converts a durable ``running`` receipt into a false
    terminal result and never calls Workable unguarded.
    """
    if not _run_is_active(org_id=int(org_id), run_id=int(run_id)):
        logger.info(
            "Workable manual sync duplicate/stale delivery skipped "
            "org_id=%s run_id=%s",
            org_id,
            run_id,
        )
        return
    handle = _acquire_workable_org_mutex(
        int(org_id),
        source=f"manual_sync:{int(run_id)}",
        heartbeat=True,
    )
    if handle is None or handle is False:
        raise task.retry(
            countdown=_retry_countdown(getattr(task.request, "retries", 0))
        )
    try:
        # The run may have completed while this delivery was contending for
        # the mutex. Recheck after ownership is established before provider I/O.
        if not _run_is_active(org_id=int(org_id), run_id=int(run_id)):
            return
        if _workable_mutex_ownership_lost(handle):
            raise task.retry(
                countdown=_retry_countdown(getattr(task.request, "retries", 0))
            )
        execute_workable_sync_run(
            org_id=int(org_id),
            run_id=int(run_id),
            mode=mode,
            selected_job_shortcodes=selected_job_shortcodes,
            should_yield=lambda: _workable_mutex_ownership_lost(handle),
        )
        if _workable_mutex_ownership_lost(handle):
            # The runner owns durable per-entity/run reconciliation. A provider
            # call may already have completed, so never replay on this signal.
            logger.warning(
                "Workable manual sync mutex lease became uncertain during run "
                "org_id=%s run_id=%s",
                org_id,
                run_id,
            )
    finally:
        _release_workable_org_mutex(handle)
