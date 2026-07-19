"""Provider-free terminal reconciliation for cross-role scoring backfills."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import exists
from sqlalchemy.orm import aliased

from ..domains.assessments_runtime.scoring_batch_state import (
    scoring_batch_exact_terminal_breakdown,
    scoring_uses_exact_receipts,
)
from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..platform.database import SessionLocal
from .scoring_backfill_recovery import (
    SCORING_BACKFILL_ACTIVE_STATUSES,
    scoring_backfill_fanout_accounted,
    scoring_backfill_plan_from_counters,
)
from .scoring_batch_fanout_recovery import SCORING_QUEUE_CONTRACT
from .scoring_batch_terminal_contract import exact_scoring_terminal_counts


_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
_SCAN_FLOOR = 100
_SCAN_CAP = 1_000
_QUERY_CHUNK_SIZE = 500
_INVALID_RECEIPTS_ERROR = "scoring_backfill_terminal_receipts_invalid"


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _exact_targets(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None
    normalized = sorted({item for item in value if type(item) is int and item > 0})
    if not normalized or normalized != value:
        return None
    return normalized


def _chunks(values: list[int]):
    for offset in range(0, len(values), _QUERY_CHUNK_SIZE):
        yield values[offset : offset + _QUERY_CHUNK_SIZE]


def _child_entries(
    counters: Mapping[str, Any],
    *,
    planned_roles: set[int],
) -> dict[int, dict[str, Any]] | None:
    raw_children = counters.get("children")
    if not isinstance(raw_children, list) or len(raw_children) != len(planned_roles):
        return None
    entries: dict[int, dict[str, Any]] = {}
    run_ids: set[int] = set()
    for raw in raw_children:
        if not isinstance(raw, Mapping):
            return None
        role_id = _positive_int(raw.get("role_id"))
        run_id = _positive_int(raw.get("run_id"))
        target = _positive_int(raw.get("target"))
        if (
            role_id is None
            or run_id is None
            or target is None
            or role_id in entries
            or run_id in run_ids
        ):
            return None
        entries[role_id] = dict(raw)
        run_ids.add(run_id)
    if set(entries) != planned_roles:
        return None
    return entries


def _load_children(db, run_ids: list[int]) -> dict[int, BackgroundJobRun]:
    children: dict[int, BackgroundJobRun] = {}
    for chunk in _chunks(run_ids):
        for child in (
            db.query(BackgroundJobRun).filter(BackgroundJobRun.id.in_(chunk)).all()
        ):
            children[int(child.id)] = child
    return children


def _invalid_result(
    counters: dict[str, Any],
    *,
    total_target: int,
) -> tuple[str, dict[str, Any], str]:
    counters.update(
        total_scored=0,
        total_pre_screened_out=0,
        total_errors=total_target,
        total_not_processed=0,
    )
    return "failed", counters, _INVALID_RECEIPTS_ERROR


def _terminal_status(
    db,
    parent: BackgroundJobRun,
) -> tuple[str | None, dict[str, Any], str | None]:
    counters = dict(parent.counters) if isinstance(parent.counters, dict) else {}
    plan = scoring_backfill_plan_from_counters(counters)
    if plan is None:
        return _invalid_result(counters, total_target=0)
    total_target = sum(len(entry["target_application_ids"]) for entry in plan)
    if not scoring_backfill_fanout_accounted(counters):
        return _invalid_result(counters, total_target=total_target)

    planned_roles = {int(entry["role_id"]) for entry in plan}
    entries = _child_entries(counters, planned_roles=planned_roles)
    if entries is None:
        return _invalid_result(counters, total_target=total_target)
    children = _load_children(
        db,
        [int(entries[int(entry["role_id"])]["run_id"]) for entry in plan],
    )

    total_scored = total_errors = total_pre_screened_out = 0
    total_not_processed = 0
    child_statuses: set[str] = set()
    for plan_entry in plan:
        role_id = int(plan_entry["role_id"])
        targets = list(plan_entry["target_application_ids"])
        entry = entries[role_id]
        if int(entry["target"]) != len(targets):
            return _invalid_result(counters, total_target=total_target)
        child = children.get(int(entry["run_id"]))
        child_counters = (
            dict(child.counters)
            if child is not None and isinstance(child.counters, dict)
            else {}
        )
        if (
            child is None
            or child.kind != JOB_KIND_SCORING_BATCH
            or child.scope_kind != SCOPE_KIND_ROLE
            or int(child.scope_id) != role_id
            or int(child.organization_id) != int(parent.organization_id)
            or child.dispatch_key != f"scoring-backfill:{int(parent.id)}:{role_id}"
            or _positive_int(child_counters.get("backfill_parent_run_id"))
            != int(parent.id)
            or child_counters.get("queue_contract") != SCORING_QUEUE_CONTRACT
            or _exact_targets(child_counters.get("target_application_ids")) != targets
            or not scoring_uses_exact_receipts(child_counters)
        ):
            return _invalid_result(counters, total_target=total_target)
        breakdown = scoring_batch_exact_terminal_breakdown(
            db,
            run_id=int(child.id),
            progress=child_counters,
        )
        raw_dispatched_ids = child_counters.get("dispatched_application_ids")
        if not isinstance(raw_dispatched_ids, list) or any(
            type(value) is not int or value <= 0 for value in raw_dispatched_ids
        ):
            return _invalid_result(counters, total_target=total_target)
        dispatched_ids = frozenset(raw_dispatched_ids)
        observed_ids = (
            breakdown.terminal_application_ids | breakdown.active_application_ids
        )
        if not observed_ids <= dispatched_ids:
            return _invalid_result(counters, total_target=total_target)
        if breakdown.active_application_ids:
            return None, counters, None

        child_status = str(child.status or "")
        if child_status not in _TERMINAL_STATUSES:
            return None, counters, None
        if dispatched_ids != breakdown.terminal_application_ids:
            return _invalid_result(counters, total_target=total_target)
        exact = exact_scoring_terminal_counts(
            child_counters,
            scored=breakdown.scored,
            errors=breakdown.errors + breakdown.cancelled,
            pre_screened_out=breakdown.pre_screened_out,
        )
        if exact is None:
            return _invalid_result(counters, total_target=total_target)
        deficit = exact.target_total - exact.accounted
        errors = breakdown.errors
        if child_status == "cancelled":
            total_not_processed += breakdown.cancelled + exact.not_enqueued + deficit
        else:
            errors += breakdown.cancelled + exact.not_enqueued + deficit
        if deficit and child_status != "cancelled":
            child_status = "failed"

        total_scored += exact.scored
        total_errors += errors
        total_pre_screened_out += exact.pre_screened_out
        child_statuses.add(child_status)

    counters.update(
        total_scored=total_scored,
        total_pre_screened_out=total_pre_screened_out,
        total_errors=total_errors,
        total_not_processed=total_not_processed,
    )
    if "failed" in child_statuses:
        return "failed", counters, None
    if "cancelled" in child_statuses:
        return "cancelled", counters, None
    return "completed", counters, None


def reconcile_scoring_backfill_parents(*, limit: int = 25) -> dict[str, int]:
    """Terminalize drained parent receipts without polling or provider work."""

    bounded_limit = max(1, min(limit, 100)) if type(limit) is int else 25
    scan_limit = min(_SCAN_CAP, max(_SCAN_FLOOR, bounded_limit * 4))
    result = {
        "examined": 0,
        "active": 0,
        "completed": 0,
        "cancelled": 0,
        "failed": 0,
        "invalid": 0,
    }
    child = aliased(BackgroundJobRun)
    has_active_child = exists().where(
        child.kind == JOB_KIND_SCORING_BATCH,
        child.scope_kind == SCOPE_KIND_ROLE,
        child.organization_id == BackgroundJobRun.organization_id,
        child.status.in_(SCORING_BACKFILL_ACTIVE_STATUSES),
        child.finished_at.is_(None),
        child.counters["backfill_parent_run_id"].as_integer() == BackgroundJobRun.id,
    )
    with SessionLocal() as db:
        parents = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ORG,
                BackgroundJobRun.status.in_(SCORING_BACKFILL_ACTIVE_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.counters["backfill_parent"].as_boolean().is_(True),
                BackgroundJobRun.counters["fanout_complete"].as_boolean().is_(True),
            )
            .order_by(has_active_child.asc(), BackgroundJobRun.id.asc())
            .limit(scan_limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        terminalized = 0
        for parent in parents:
            result["examined"] += 1
            status, counters, error = _terminal_status(db, parent)
            if status is None:
                result["active"] += 1
                continue
            parent.status = status
            parent.counters = counters
            parent.finished_at = datetime.now(timezone.utc)
            if error is not None:
                parent.error = error
                result["invalid"] += 1
            result[status] += 1
            terminalized += 1
            if terminalized >= bounded_limit:
                break
        db.commit()
    return result


__all__ = ["reconcile_scoring_backfill_parents"]
