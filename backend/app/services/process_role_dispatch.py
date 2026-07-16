"""Durable dispatch and worker leases for the recruiter Process cascade.

The Process action can fan out several paid/provider-backed operations.  Its
durability contract is deliberately conservative:

* persist the exact recruiter-authorized payload before touching the broker;
* recover messages that were never accepted (or were accepted ambiguously);
* allow only one worker delivery to own the run;
* checkpoint progress and renew the worker lease with the domain writes; and
* never automatically replay an expired running worker, because it may have
  died immediately after a provider accepted a non-idempotent request.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..models.background_job_run import (
    JOB_KIND_PROCESS_ROLE,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)


logger = logging.getLogger("taali.process_role_dispatch")

PROCESS_ACTIVE_STATUSES = ("dispatching", "queued", "running", "cancelling")
PROCESS_PUBLISHABLE_STATUSES = ("dispatching", "queued")
PROCESS_WORKER_STATUSES = ("running", "cancelling")
PROCESS_PUBLISH_RETRY = timedelta(minutes=2)
PROCESS_QUEUED_RECOVERY_DELAY = timedelta(minutes=15)
PROCESS_WORKER_LEASE = timedelta(minutes=30)

_PROGRESS_KEY = "progress"
_RECOVERY_PAYLOAD_KEY = "recovery_payload"
_DISPATCH_ATTEMPTS_KEY = "dispatch_attempts"
_DISPATCH_NEXT_ATTEMPT_KEY = "dispatch_next_attempt_at"
_LAST_DISPATCHED_AT_KEY = "last_dispatched_at"
_WORKER_ATTEMPTS_KEY = "worker_attempts"
_WORKER_HEARTBEAT_KEY = "worker_heartbeat_at"
_WORKER_LEASE_KEY = "worker_lease_expires_at"


class ProcessRoleDispatchError(RuntimeError):
    """Raised when durable Process state cannot be safely advanced."""


@dataclass(frozen=True)
class ProcessRoleIntent:
    run: BackgroundJobRun
    created: bool


@dataclass(frozen=True)
class ProcessRoleWorkerClaim:
    state: str
    run_id: int
    role_id: int
    organization_id: int
    payload: dict[str, Any] | None
    progress: dict[str, Any]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _copy_progress(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def progress_from_run(run: BackgroundJobRun) -> dict[str, Any]:
    """Return the recruiter-facing progress snapshot for a durable run."""

    counters = run.counters if isinstance(run.counters, dict) else {}
    progress = _copy_progress(counters.get(_PROGRESS_KEY))
    durable_status = str(run.status or "")
    if durable_status in {"completed", "cancelled", "failed"}:
        progress["status"] = durable_status
    elif durable_status == "cancelling":
        progress["status"] = "cancelling"
    elif durable_status in {"dispatching", "queued"}:
        progress["status"] = "queued"
    elif durable_status == "running":
        progress["status"] = "running"
    progress["run_id"] = int(run.id)
    if run.error:
        # BackgroundJobRun stores bounded public failure codes, never provider
        # exception text.  The toaster turns these into recruiter-safe copy.
        progress["error"] = str(run.error)
        progress["error_message"] = str(run.error)
    return progress


def _payload_from_run(run: BackgroundJobRun) -> dict[str, Any]:
    counters = run.counters if isinstance(run.counters, dict) else {}
    payload = counters.get(_RECOVERY_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        raise ProcessRoleDispatchError("process recovery payload is missing")
    result = copy.deepcopy(payload)
    if (
        int(result.get("role_id") or 0) != int(run.scope_id)
        or int(result.get("organization_id") or 0) != int(run.organization_id)
    ):
        raise ProcessRoleDispatchError("process recovery payload scope mismatch")
    return result


def ensure_process_role_intent(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    payload: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> ProcessRoleIntent:
    """Create one active Process intent for an organization/role scope.

    PostgreSQL advisory locking closes the no-row-yet race.  The row remains
    the durable source of truth; Redis is only a cross-process display cache.
    """

    role_id = int(role_id)
    organization_id = int(organization_id)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('process_role_dispatch'), :role_id)"
            ),
            {"role_id": role_id},
        )
    existing = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == role_id,
            BackgroundJobRun.organization_id == organization_id,
            BackgroundJobRun.status.in_(PROCESS_ACTIVE_STATUSES),
            BackgroundJobRun.finished_at.is_(None),
        )
        .order_by(BackgroundJobRun.id.desc())
        .with_for_update()
        .first()
    )
    if existing is not None:
        return ProcessRoleIntent(run=existing, created=False)

    recovery_payload = copy.deepcopy(dict(payload))
    recovery_payload["role_id"] = role_id
    recovery_payload["organization_id"] = organization_id
    run = BackgroundJobRun(
        kind=JOB_KIND_PROCESS_ROLE,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role_id,
        organization_id=organization_id,
        dispatch_key=(
            f"process-role:{organization_id}:{role_id}:{uuid4().hex}"
        ),
        status="dispatching",
        counters={
            _PROGRESS_KEY: _copy_progress(dict(progress)),
            _RECOVERY_PAYLOAD_KEY: recovery_payload,
        },
    )
    db.add(run)
    db.flush()
    return ProcessRoleIntent(run=run, created=True)


def claim_process_publish(
    run: BackgroundJobRun,
    *,
    now: datetime | None = None,
) -> dict[str, int] | None:
    """Reserve the next broker publish window before broker I/O."""

    if str(run.status) not in PROCESS_PUBLISHABLE_STATUSES:
        return None
    current = _as_utc(now or datetime.now(timezone.utc))
    counters = dict(run.counters or {})
    next_attempt = _parse_datetime(counters.get(_DISPATCH_NEXT_ATTEMPT_KEY))
    if next_attempt is not None and next_attempt > current:
        return None
    # Validate the persisted authority before reserving a delivery.
    _payload_from_run(run)
    attempts = int(counters.get(_DISPATCH_ATTEMPTS_KEY) or 0) + 1
    counters[_DISPATCH_ATTEMPTS_KEY] = attempts
    retry_seconds = min(
        int(PROCESS_PUBLISH_RETRY.total_seconds())
        * (2 ** min(max(0, attempts - 1), 4)),
        int(PROCESS_QUEUED_RECOVERY_DELAY.total_seconds()),
    )
    counters[_DISPATCH_NEXT_ATTEMPT_KEY] = (
        current + timedelta(seconds=retry_seconds)
    ).isoformat()
    run.counters = counters
    return {"run_id": int(run.id)}


def process_publish_due_filter(*, now: datetime | None = None):
    """Portable SQL predicate for publishable Process intents that are due."""

    current = _as_utc(now or datetime.now(timezone.utc)).isoformat()
    next_attempt = BackgroundJobRun.counters[_DISPATCH_NEXT_ATTEMPT_KEY].as_string()
    return or_(next_attempt.is_(None), next_attempt <= current)


def mark_process_dispatched(
    db: Session,
    *,
    run_id: int,
    now: datetime | None = None,
) -> bool:
    """Record broker acceptance without overwriting a faster worker claim."""

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
        )
        .with_for_update()
        .one_or_none()
    )
    if run is None or str(run.status) not in PROCESS_PUBLISHABLE_STATUSES:
        return False
    current = _as_utc(now or datetime.now(timezone.utc))
    counters = dict(run.counters or {})
    counters[_LAST_DISPATCHED_AT_KEY] = current.isoformat()
    counters[_DISPATCH_NEXT_ATTEMPT_KEY] = (
        current + PROCESS_QUEUED_RECOVERY_DELAY
    ).isoformat()
    run.counters = counters
    run.status = "queued"
    return True


def claim_process_worker(
    db: Session,
    *,
    run_id: int,
    now: datetime | None = None,
) -> ProcessRoleWorkerClaim:
    """Claim a durable Process run for exactly one worker delivery.

    An expired running lease is failed, not replayed.  That distinction is
    essential for pre-screen/Graph provider calls whose acceptance can be
    ambiguous when a worker is force-killed.
    """

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
        )
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise ProcessRoleDispatchError("process run not found")
    current = _as_utc(now or datetime.now(timezone.utc))
    status = str(run.status or "")
    progress = progress_from_run(run)
    if status in PROCESS_WORKER_STATUSES:
        lease_expires = _parse_datetime(
            dict(run.counters or {}).get(_WORKER_LEASE_KEY)
        )
        if lease_expires is None or lease_expires <= current:
            progress["status"] = "failed"
            progress["error"] = "process_worker_lost"
            counters = dict(run.counters or {})
            counters[_PROGRESS_KEY] = _copy_progress(progress)
            run.counters = counters
            run.status = "failed"
            run.error = "process_worker_lost"
            run.finished_at = current
            return ProcessRoleWorkerClaim(
                state="worker_lost",
                run_id=int(run.id),
                role_id=int(run.scope_id),
                organization_id=int(run.organization_id),
                payload=None,
                progress=progress,
            )
        return ProcessRoleWorkerClaim(
            state="already_running",
            run_id=int(run.id),
            role_id=int(run.scope_id),
            organization_id=int(run.organization_id),
            payload=None,
            progress=progress,
        )
    if status not in PROCESS_PUBLISHABLE_STATUSES:
        return ProcessRoleWorkerClaim(
            state="terminal",
            run_id=int(run.id),
            role_id=int(run.scope_id),
            organization_id=int(run.organization_id),
            payload=None,
            progress=progress,
        )

    payload = _payload_from_run(run)
    counters = dict(run.counters or {})
    progress["status"] = "running"
    counters[_PROGRESS_KEY] = _copy_progress(progress)
    counters[_WORKER_ATTEMPTS_KEY] = int(counters.get(_WORKER_ATTEMPTS_KEY) or 0) + 1
    counters[_WORKER_HEARTBEAT_KEY] = current.isoformat()
    counters[_WORKER_LEASE_KEY] = (current + PROCESS_WORKER_LEASE).isoformat()
    run.counters = counters
    run.status = "running"
    return ProcessRoleWorkerClaim(
        state="claimed",
        run_id=int(run.id),
        role_id=int(run.scope_id),
        organization_id=int(run.organization_id),
        payload=payload,
        progress=progress,
    )


def checkpoint_process_run(
    db: Session,
    *,
    run_id: int,
    progress: Mapping[str, Any],
    now: datetime | None = None,
) -> None:
    """Commit domain changes together with progress and a renewed lease."""

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if run is None or str(run.status) not in PROCESS_WORKER_STATUSES:
        raise ProcessRoleDispatchError("process worker no longer owns the run")
    current = _as_utc(now or datetime.now(timezone.utc))
    snapshot = _copy_progress(dict(progress))
    snapshot_status = str(snapshot.get("status") or "running")
    counters = dict(run.counters or {})
    counters[_PROGRESS_KEY] = snapshot
    counters[_WORKER_HEARTBEAT_KEY] = current.isoformat()
    counters[_WORKER_LEASE_KEY] = (current + PROCESS_WORKER_LEASE).isoformat()
    run.counters = counters
    if snapshot_status in {"completed", "cancelled", "failed"}:
        run.status = snapshot_status
        run.finished_at = current
        if snapshot_status == "failed" and not run.error:
            run.error = str(snapshot.get("error") or "process_failed")[:200]
    elif snapshot_status == "cancelling" or run.cancel_requested_at is not None:
        run.status = "cancelling"
    else:
        run.status = "running"
    db.commit()


def fail_process_run(
    db: Session,
    *,
    run_id: int,
    progress: Mapping[str, Any] | None = None,
    error_code: str = "process_failed",
    now: datetime | None = None,
) -> bool:
    """Fail an owned/non-terminal run using a bounded public error code."""

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
            BackgroundJobRun.finished_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        return False
    current = _as_utc(now or datetime.now(timezone.utc))
    snapshot = _copy_progress(progress) or progress_from_run(run)
    snapshot["status"] = "failed"
    snapshot["error"] = str(error_code)[:200]
    counters = dict(run.counters or {})
    counters[_PROGRESS_KEY] = snapshot
    run.counters = counters
    run.status = "failed"
    run.error = str(error_code)[:200]
    run.finished_at = current
    return True


def process_cancel_requested(db: Session, *, run_id: int) -> bool:
    row = (
        db.query(BackgroundJobRun.cancel_requested_at)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
        )
        .one_or_none()
    )
    return bool(row is not None and row[0] is not None)


def request_process_cancel(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    now: datetime | None = None,
) -> BackgroundJobRun | None:
    """Durably cancel a queued run or request cooperative worker cancellation."""

    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role_id),
            BackgroundJobRun.organization_id == int(organization_id),
            BackgroundJobRun.status.in_(PROCESS_ACTIVE_STATUSES),
            BackgroundJobRun.finished_at.is_(None),
        )
        .order_by(BackgroundJobRun.id.desc())
        .with_for_update()
        .first()
    )
    if run is None:
        return None
    current = _as_utc(now or datetime.now(timezone.utc))
    if run.cancel_requested_at is None:
        run.cancel_requested_at = current
    progress = progress_from_run(run)
    counters = dict(run.counters or {})
    if str(run.status) in PROCESS_PUBLISHABLE_STATUSES:
        progress["status"] = "cancelled"
        run.status = "cancelled"
        run.finished_at = current
    else:
        progress["status"] = "cancelling"
        run.status = "cancelling"
    counters[_PROGRESS_KEY] = progress
    run.counters = counters
    return run


def latest_process_role_run(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
) -> BackgroundJobRun | None:
    return (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role_id),
            BackgroundJobRun.organization_id == int(organization_id),
        )
        .order_by(BackgroundJobRun.id.desc())
        .first()
    )


def expire_stale_process_workers(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """Fail expired workers without replaying ambiguous provider work."""

    current = _as_utc(now or datetime.now(timezone.utc))
    lease = BackgroundJobRun.counters[_WORKER_LEASE_KEY].as_string()
    rows = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_PROCESS_ROLE,
            BackgroundJobRun.status.in_(PROCESS_WORKER_STATUSES),
            BackgroundJobRun.finished_at.is_(None),
            or_(lease.is_(None), lease <= current.isoformat()),
        )
        .order_by(BackgroundJobRun.id.asc())
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
        .all()
    )
    expired: list[tuple[int, dict[str, Any]]] = []
    for run in rows:
        progress = progress_from_run(run)
        counters = dict(run.counters or {})
        if run.cancel_requested_at is not None:
            progress["status"] = "cancelled"
            run.status = "cancelled"
            run.error = None
        else:
            progress["status"] = "failed"
            progress["error"] = "process_worker_lost"
            run.status = "failed"
            run.error = "process_worker_lost"
        counters[_PROGRESS_KEY] = progress
        run.counters = counters
        run.finished_at = current
        expired.append((int(run.scope_id), progress))
    return expired


__all__ = [
    "PROCESS_ACTIVE_STATUSES",
    "PROCESS_PUBLISHABLE_STATUSES",
    "PROCESS_PUBLISH_RETRY",
    "PROCESS_QUEUED_RECOVERY_DELAY",
    "PROCESS_WORKER_LEASE",
    "ProcessRoleDispatchError",
    "ProcessRoleIntent",
    "ProcessRoleWorkerClaim",
    "checkpoint_process_run",
    "claim_process_publish",
    "claim_process_worker",
    "ensure_process_role_intent",
    "expire_stale_process_workers",
    "fail_process_run",
    "latest_process_role_run",
    "mark_process_dispatched",
    "process_cancel_requested",
    "process_publish_due_filter",
    "progress_from_run",
    "request_process_cancel",
]
