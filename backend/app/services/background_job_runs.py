"""Helpers for recording in-memory job kinds (scoring batch, CV fetch, graph
sync) into ``background_job_runs`` so the Settings → Background jobs panel
can render history beyond the current in-process state.

These helpers swallow exceptions: a failed bookkeeping write must never
break the actual job. The in-memory dict remains the source of truth for
live progress; the row is the source of truth for history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from ..models.background_job_run import (
    BackgroundJobRun,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
)
from ..platform.database import SessionLocal


logger = logging.getLogger(__name__)


def create_run(
    *,
    kind: str,
    scope_kind: str,
    scope_id: int,
    organization_id: int,
    counters: Mapping[str, Any] | None = None,
    status: str = "running",
) -> int | None:
    """Insert a new background_job_runs row. Returns the new id, or None on failure."""
    db = SessionLocal()
    try:
        row = BackgroundJobRun(
            kind=kind,
            scope_kind=scope_kind,
            scope_id=int(scope_id),
            organization_id=int(organization_id),
            status=status,
            counters=dict(counters or {}),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    except Exception:
        logger.exception("background_job_runs: create failed")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()


def update_run(
    run_id: int | None,
    *,
    status: str | None = None,
    counters: Mapping[str, Any] | None = None,
    error: str | None = None,
    finished: bool = False,
    cancel_requested: bool = False,
) -> None:
    """Update an existing run row. Silent no-op when run_id is None."""
    if not run_id:
        return
    db = SessionLocal()
    try:
        row = db.query(BackgroundJobRun).filter(BackgroundJobRun.id == run_id).first()
        if row is None:
            return
        if status is not None:
            row.status = status
        if counters is not None:
            row.counters = dict(counters)
        if error is not None:
            row.error = error
        now = datetime.now(timezone.utc)
        if finished:
            row.finished_at = now
        if cancel_requested and row.cancel_requested_at is None:
            row.cancel_requested_at = now
        db.commit()
    except Exception:
        logger.exception("background_job_runs: update failed for id=%s", run_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def mark_dispatched(run_id: int | None) -> bool:
    """Atomically move a replayable op from dispatching to queued.

    A very fast worker may already have changed the row to ``running``; the
    conditional update intentionally leaves that newer state untouched.
    """
    if not run_id:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.status == "dispatching",
                BackgroundJobRun.finished_at.is_(None),
            )
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        counters["last_dispatched_at"] = datetime.now(timezone.utc).isoformat()
        row.counters = counters
        row.status = "queued"
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: dispatch mark failed id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


def mark_running(run_id: int | None) -> bool:
    """Claim a replayable delivery unless its run already reached terminal state.

    Duplicate broker deliveries serialize on the provider mutex, then collapse
    here. A stale recovery delivery that arrives after the original completed
    therefore cannot repeat even an idempotent remote status write.
    """

    if not run_id:
        return True
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.status.in_(("dispatching", "queued", "running")),
                BackgroundJobRun.finished_at.is_(None),
            )
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        counters["last_started_at"] = datetime.now(timezone.utc).isoformat()
        counters["delivery_attempts"] = int(counters.get("delivery_attempts") or 0) + 1
        row.counters = counters
        row.status = "running"
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: running claim failed id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


__all__ = [
    "create_run",
    "update_run",
    "mark_dispatched",
    "mark_running",
    "SCOPE_KIND_ROLE",
    "SCOPE_KIND_ORG",
]
