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


__all__ = [
    "create_run",
    "update_run",
    "SCOPE_KIND_ROLE",
    "SCOPE_KIND_ORG",
]
