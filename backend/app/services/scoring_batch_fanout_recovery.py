"""Durable publish receipts and bounded recovery for scoring fan-out roots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..platform.database import SessionLocal
from .scoring_recovery_audit import (
    json_boolean_false_or_missing,
    json_not_boolean_true,
    mark_recovery_audited,
    recovery_audit_due,
    recovery_audit_order,
)


SCORING_QUEUE_CONTRACT = "background_job_run_successor_v1"
SCORING_FANOUT_ACTIVE_STATUSES = (
    "dispatching",
    "queued",
    "running",
    "cancelling",
)
_PUBLISH_RETRY_BASE_SECONDS = 60
_PUBLISH_RETRY_MAX_SECONDS = 300
_PUBLISHED_RECOVERY_SECONDS = 120
_RECOVERY_SCAN_FLOOR = 100
_RECOVERY_SCAN_CAP = 1_000
_MAX_FUTURE_METADATA_SECONDS = 15 * 60
_INVALID_FANOUT_ERROR = "scoring_batch_invalid_fanout_recovery_contract"
_FANOUT_AUDIT_KEY = "fanout_recovery_audited_at"
_RECOVERY_AUDIT_SECONDS = 10 * 60


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError:
        return None
    return _as_utc(parsed)


def _progress_count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _bounded_limit(value: object) -> int:
    return max(1, min(value, 100)) if type(value) is int else 25


def _fanout_contract_error(
    run: BackgroundJobRun,
    *,
    now: datetime | None = None,
) -> str | None:
    """Return a stable reason only when a durable root cannot be replayed."""

    if not isinstance(run.counters, dict):
        return "counters_not_object"
    counters = run.counters
    if counters.get("queue_contract") != SCORING_QUEUE_CONTRACT:
        return "unsupported_queue_contract"
    raw_targets = counters.get("target_application_ids")
    if (
        not isinstance(raw_targets, list)
        or not raw_targets
        or any(type(item) is not int or item <= 0 for item in raw_targets)
    ):
        return "invalid_target_application_ids"
    if type(counters.get("include_scored")) is not bool:
        return "invalid_include_scored"
    applied_after = counters.get("applied_after")
    if applied_after is not None and type(applied_after) is not str:
        return "invalid_applied_after"
    fanout_complete = counters.get("fanout_complete")
    if fanout_complete is not None and type(fanout_complete) is not bool:
        return "invalid_fanout_complete"
    attempts = counters.get("fanout_dispatch_attempts")
    if attempts is not None and (type(attempts) is not int or attempts < 0):
        return "invalid_dispatch_attempts"
    current = _as_utc(now or datetime.now(timezone.utc))
    for key in (
        "fanout_dispatch_next_attempt_at",
        "fanout_lease_expires_at",
    ):
        value = counters.get(key)
        if value is None:
            continue
        parsed = _parse_datetime(value)
        if parsed is None:
            return f"invalid_{key}"
        if parsed > current + timedelta(seconds=_MAX_FUTURE_METADATA_SECONDS):
            return f"invalid_future_{key}"
    return None


def _quarantine_invalid_fanout(
    run: BackgroundJobRun,
    *,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Terminalize an unreplayable durable root while preserving its evidence."""

    current = _as_utc(now or datetime.now(timezone.utc))
    counters = dict(run.counters) if isinstance(run.counters, dict) else {}
    counters.update(
        fanout_complete=True,
        fanout_state="invalid_recovery_contract",
        fanout_quarantined_at=current.isoformat(),
        fanout_quarantine_reason=reason,
    )
    run.counters = counters
    run.status = "failed"
    run.error = _INVALID_FANOUT_ERROR
    run.finished_at = current


def _exact_run_query(
    db: Session,
    *,
    run_id: int,
    role_id: int,
    organization_id: int,
):
    return db.query(BackgroundJobRun).filter(
        BackgroundJobRun.id == int(run_id),
        BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
        BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
        BackgroundJobRun.scope_id == int(role_id),
        BackgroundJobRun.organization_id == int(organization_id),
    )


def scoring_fanout_publish_due_filter(
    db: Session,
    *,
    now: datetime | None = None,
):
    """Portable SQL predicate for unfinished fan-outs whose lease is due."""

    current = _as_utc(now or datetime.now(timezone.utc)).isoformat()
    next_attempt = BackgroundJobRun.counters[
        "fanout_dispatch_next_attempt_at"
    ].as_string()
    lease = BackgroundJobRun.counters["fanout_lease_expires_at"].as_string()
    return (
        json_boolean_false_or_missing(db, "fanout_complete"),
        or_(next_attempt.is_(None), next_attempt <= current),
        or_(lease.is_(None), lease <= current),
    )


def claim_scoring_fanout_publish(
    run: BackgroundJobRun,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Reserve one publish window on an already locked durable run."""

    if (
        run.kind != JOB_KIND_SCORING_BATCH
        or run.scope_kind != SCOPE_KIND_ROLE
        or run.finished_at is not None
        or str(run.status) not in SCORING_FANOUT_ACTIVE_STATUSES
        or not isinstance(run.counters, dict)
    ):
        return None
    if _fanout_contract_error(run, now=now) is not None:
        return None
    counters = dict(run.counters)
    if bool(counters.get("fanout_complete")):
        return None
    include_scored = counters.get("include_scored")
    applied_after = counters.get("applied_after")
    if type(include_scored) is not bool:
        return None
    if applied_after is not None and type(applied_after) is not str:
        return None
    if applied_after and _parse_datetime(applied_after) is None:
        # The immutable target snapshot is authoritative. Corrupt historical
        # filter display metadata must neither broaden nor kill exact work.
        applied_after = None

    current = _as_utc(now or datetime.now(timezone.utc))
    next_attempt = _parse_datetime(counters.get("fanout_dispatch_next_attempt_at"))
    lease_expires = _parse_datetime(counters.get("fanout_lease_expires_at"))
    if next_attempt is not None and next_attempt > current:
        return None
    if lease_expires is not None and lease_expires > current:
        return None

    attempts = _progress_count(counters.get("fanout_dispatch_attempts")) + 1
    retry_seconds = min(
        _PUBLISH_RETRY_BASE_SECONDS * (2 ** min(max(0, attempts - 1), 3)),
        _PUBLISH_RETRY_MAX_SECONDS,
    )
    counters.update(
        fanout_complete=False,
        fanout_dispatch_attempts=attempts,
        fanout_last_publish_claimed_at=current.isoformat(),
        fanout_dispatch_next_attempt_at=(
            current + timedelta(seconds=retry_seconds)
        ).isoformat(),
    )
    run.counters = counters
    return {
        "role_id": int(run.scope_id),
        "organization_id": int(run.organization_id),
        "include_scored": include_scored,
        "applied_after": applied_after,
        "run_id": int(run.id),
    }


def reserve_scoring_fanout_publish(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Commit a producer publish claim before touching the broker."""

    with SessionLocal() as db:
        run = (
            _exact_run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        payload = (
            claim_scoring_fanout_publish(run, now=now) if run is not None else None
        )
        db.commit()
        return payload


def _mark_scoring_fanout_publish(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    published: bool,
    now: datetime | None = None,
) -> bool:
    with SessionLocal() as db:
        run = (
            _exact_run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None or run.finished_at is not None:
            return False
        current = _as_utc(now or datetime.now(timezone.utc))
        counters = dict(run.counters or {})
        if published:
            counters["fanout_last_published_at"] = current.isoformat()
            counters["fanout_dispatch_next_attempt_at"] = (
                current + timedelta(seconds=_PUBLISHED_RECOVERY_SECONDS)
            ).isoformat()
            counters.pop("fanout_last_publish_error", None)
            if run.status == "dispatching":
                run.status = "queued"
        else:
            counters["fanout_last_publish_error"] = "broker_publish_failed"
        run.counters = counters
        db.commit()
        return True


def mark_scoring_fanout_published(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    now: datetime | None = None,
) -> bool:
    return _mark_scoring_fanout_publish(
        run_id,
        role_id=role_id,
        organization_id=organization_id,
        published=True,
        now=now,
    )


def mark_scoring_fanout_publish_failed(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    now: datetime | None = None,
) -> bool:
    return _mark_scoring_fanout_publish(
        run_id,
        role_id=role_id,
        organization_id=organization_id,
        published=False,
        now=now,
    )


def claim_due_scoring_fanouts(
    db: Session,
    *,
    limit: int = 25,
    now: datetime | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Lock and reserve a bounded set of lost or expired root deliveries."""

    bounded_limit = _bounded_limit(limit)
    scan_limit = min(
        _RECOVERY_SCAN_CAP,
        max(_RECOVERY_SCAN_FLOOR, bounded_limit * 4),
    )
    current = _as_utc(now or datetime.now(timezone.utc))
    current_text = current.isoformat()
    audit_stale_before = (
        current - timedelta(seconds=_RECOVERY_AUDIT_SECONDS)
    ).isoformat()

    def _active_roots():
        return db.query(BackgroundJobRun).filter(
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.status.in_(SCORING_FANOUT_ACTIVE_STATUSES),
            BackgroundJobRun.finished_at.is_(None),
        )

    scanned = 0
    scanned_ids: set[int] = set()
    payloads: list[dict[str, Any]] = []

    # Audit the whole active role-run population fairly instead of trusting a
    # queue-contract marker that corruption could remove. Each valid row gets a
    # durable timestamp, so a fixed old prefix moves behind unaudited rows on
    # the next beat. Completed fan-outs never enter publish recovery.
    audit_rows = (
        _active_roots()
        .filter(
            json_not_boolean_true(db, "fanout_complete"),
            recovery_audit_due(
                db,
                _FANOUT_AUDIT_KEY,
                current=current_text,
                stale_before=audit_stale_before,
            ),
        )
        .order_by(*recovery_audit_order(db, _FANOUT_AUDIT_KEY, current=current_text))
        .limit(scan_limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    for run in audit_rows:
        contract_error = _fanout_contract_error(run, now=now)
        if contract_error is not None:
            scanned += 1
            scanned_ids.add(int(run.id))
            _quarantine_invalid_fanout(run, reason=contract_error, now=now)
            continue
        mark_recovery_audited(run, _FANOUT_AUDIT_KEY, now=current)
    db.flush()

    # Keep normal work on a narrow, bounded due path. Invalid rows outside the
    # audit window may still surface here and are quarantined rather than
    # repeatedly blocking a valid tail.
    due_rows = (
        _active_roots()
        .filter(
            BackgroundJobRun.counters["queue_contract"].as_string()
            == SCORING_QUEUE_CONTRACT,
            *scoring_fanout_publish_due_filter(db, now=now),
        )
        .order_by(BackgroundJobRun.id.asc())
        .limit(scan_limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    for run in due_rows:
        contract_error = _fanout_contract_error(run, now=now)
        if contract_error is not None:
            if int(run.id) not in scanned_ids:
                scanned += 1
                scanned_ids.add(int(run.id))
            _quarantine_invalid_fanout(run, reason=contract_error, now=now)
            continue
        if len(payloads) >= bounded_limit:
            continue
        payload = claim_scoring_fanout_publish(run, now=now)
        if payload is not None:
            if int(run.id) not in scanned_ids:
                scanned += 1
                scanned_ids.add(int(run.id))
            payloads.append(payload)
    return scanned, payloads


__all__ = [
    "claim_due_scoring_fanouts",
    "claim_scoring_fanout_publish",
    "mark_scoring_fanout_publish_failed",
    "mark_scoring_fanout_published",
    "reserve_scoring_fanout_publish",
    "scoring_fanout_publish_due_filter",
]
