"""Durable lease state for pre-screen batch root publication.

The producer persists ``BackgroundJobRun`` before publishing Celery work. A
short JSON-counter lease closes the crash window between those two actions
without a schema migration; Beat can reclaim an unpublished or ambiguously
published root while the per-item idempotency fences remain authoritative.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_


PRESCREEN_ROOT_DISPATCH_LEASE = timedelta(minutes=10)
PRESCREEN_ROOT_DISPATCH_RETRY_DELAY = timedelta(minutes=1)
PRESCREEN_ROOT_DISPATCH_LIMIT = 25
PRESCREEN_ACTIVE_RUN_STATUSES = ("dispatching", "queued", "running")


def prior_overlapping_prescreen_run_id(db, run) -> int | None:
    """Return the first older run that was live when ``run`` was created."""
    from ..models.background_job_run import BackgroundJobRun

    row = (
        db.query(BackgroundJobRun.id)
        .filter(
            BackgroundJobRun.kind == run.kind,
            BackgroundJobRun.scope_kind == run.scope_kind,
            BackgroundJobRun.scope_id == int(run.scope_id),
            BackgroundJobRun.organization_id == int(run.organization_id),
            BackgroundJobRun.id < int(run.id),
            or_(
                and_(
                    BackgroundJobRun.finished_at.is_(None),
                    BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
                ),
                BackgroundJobRun.finished_at >= run.started_at,
            ),
        )
        .order_by(BackgroundJobRun.id)
        .first()
    )
    return int(row[0]) if row is not None else None


def prescreen_item_run_authority(db, item) -> tuple[object | None, bool, str | None]:
    """Validate an item's durable parent and return its persisted refresh bit."""
    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )

    run = db.query(BackgroundJobRun).filter(
        BackgroundJobRun.id == int(item.run_id),
        BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
        BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
        BackgroundJobRun.scope_id == int(item.role_id),
        BackgroundJobRun.organization_id == int(item.organization_id),
        BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
        BackgroundJobRun.finished_at.is_(None),
    ).one_or_none()
    if run is None:
        return None, False, "invalid_parent_run"
    if not isinstance(run.counters, dict):
        run.status = "failed"
        run.error = "pre_screen_batch_invalid_counters"
        run.finished_at = datetime.now(timezone.utc)
        return run, False, "invalid_parent_run"
    return run, bool(run.counters.get("refresh", False)), None


def prescreen_item_conflict_code(db, item, run) -> str | None:
    """Fence unresolved attempts and later runs for one application."""
    from ..models.background_job_run import BackgroundJobRun
    from ..models.prescreen_batch_item import (
        PRESCREEN_BATCH_ITEM_AMBIGUOUS,
        PRESCREEN_BATCH_ITEM_ATTEMPTING,
        PrescreenBatchItem,
    )

    unresolved = (
        db.query(PrescreenBatchItem.id)
        .join(BackgroundJobRun, BackgroundJobRun.id == PrescreenBatchItem.run_id)
        .filter(
            PrescreenBatchItem.id != int(item.id),
            PrescreenBatchItem.application_id == int(item.application_id),
            or_(
                PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_ATTEMPTING,
                and_(
                    PrescreenBatchItem.status == PRESCREEN_BATCH_ITEM_AMBIGUOUS,
                    BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
                    BackgroundJobRun.finished_at.is_(None),
                ),
            ),
        )
        .first()
    )
    if unresolved is not None:
        return "application_attempt_active"
    overlapping_terminal = db.query(PrescreenBatchItem.id).filter(
        PrescreenBatchItem.id != int(item.id),
        PrescreenBatchItem.application_id == int(item.application_id),
        PrescreenBatchItem.finished_at.isnot(None),
        PrescreenBatchItem.finished_at >= run.started_at,
    ).first()
    if (
        overlapping_terminal is not None
        or prior_overlapping_prescreen_run_id(db, run) is not None
    ):
        return "duplicate_active_run"
    return None


def root_lease_until(counters: dict) -> datetime | None:
    raw = counters.get("root_dispatch_lease_until")
    if not isinstance(raw, str) or len(raw) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def clear_root_dispatch_lease(counters: dict, *, state: str) -> dict:
    clean = dict(counters) if isinstance(counters, dict) else {}
    for key in (
        "root_dispatch_token",
        "root_dispatch_lease_until",
        "root_dispatch_error",
    ):
        clean.pop(key, None)
    clean["root_dispatch_state"] = state
    return clean


def claim_dispatchable_prescreen_runs(
    *,
    limit: int = PRESCREEN_ROOT_DISPATCH_LIMIT,
    run_id: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Lease queued root jobs so a producer crash is recoverable by Beat."""

    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )
    from ..platform.database import SessionLocal

    claimed_at = now or datetime.now(timezone.utc)
    limit = max(1, min(int(limit), PRESCREEN_ROOT_DISPATCH_LIMIT))
    db = SessionLocal()
    try:
        query = db.query(BackgroundJobRun).filter(
            BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.status.in_(("queued", "dispatching")),
            BackgroundJobRun.finished_at.is_(None),
        )
        if run_id is not None:
            query = query.filter(BackgroundJobRun.id == int(run_id))
        rows = (
            query.order_by(
                BackgroundJobRun.status.desc(), BackgroundJobRun.id
            )
            .with_for_update(of=BackgroundJobRun, skip_locked=True)
            .limit(1 if run_id is not None else min(limit * 4, 100))
            .all()
        )
        claims: list[dict] = []
        for run in rows:
            if not isinstance(run.counters, dict):
                run.status = "failed"
                run.error = "pre_screen_batch_invalid_counters"
                run.finished_at = claimed_at
                continue
            counters = dict(run.counters)
            lease_until = root_lease_until(counters)
            if (
                run.status == "dispatching"
                and counters.get("root_dispatch_lease_until") is not None
                and lease_until is None
            ):
                run.status = "failed"
                run.error = "pre_screen_batch_invalid_root_lease"
                run.finished_at = claimed_at
                continue
            if (
                run.status == "dispatching"
                and lease_until is not None
                and lease_until > claimed_at
            ):
                continue
            try:
                attempts = max(
                    0, int(counters.get("root_dispatch_attempts", 0))
                )
            except (TypeError, ValueError):
                attempts = 0
            token = str(uuid4())
            counters.update(
                root_dispatch_token=token,
                root_dispatch_lease_until=(
                    claimed_at + PRESCREEN_ROOT_DISPATCH_LEASE
                ).isoformat(),
                root_dispatch_attempts=min(attempts + 1, 1_000_000),
                root_last_dispatched_at=claimed_at.isoformat(),
                root_dispatch_state="dispatching",
            )
            run.status = "dispatching"
            run.counters = counters
            claims.append(
                {
                    "run_id": int(run.id),
                    "role_id": int(run.scope_id),
                    "organization_id": int(run.organization_id),
                    "token": token,
                    "refresh": bool(counters.get("refresh", False)),
                }
            )
            if len(claims) >= limit:
                break
        db.commit()
        return claims
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def defer_root_dispatch_claim(run_id: int, token: str) -> bool:
    from ..models.background_job_run import (
        JOB_KIND_PRE_SCREEN_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        run = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.status == "dispatching",
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        counters = dict(run.counters or {}) if run is not None else {}
        if run is None or counters.get("root_dispatch_token") != str(token):
            db.rollback()
            return False
        counters["root_dispatch_lease_until"] = (
            datetime.now(timezone.utc) + PRESCREEN_ROOT_DISPATCH_RETRY_DELAY
        ).isoformat()
        counters["root_dispatch_error"] = "broker_dispatch_retry"
        run.counters = counters
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


__all__ = [
    "PRESCREEN_ROOT_DISPATCH_LIMIT",
    "claim_dispatchable_prescreen_runs",
    "clear_root_dispatch_lease",
    "defer_root_dispatch_claim",
    "root_lease_until",
]
