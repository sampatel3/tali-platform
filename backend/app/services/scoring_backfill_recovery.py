"""Durable, idempotent fan-out recovery for cross-role scoring backfills."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..platform.database import SessionLocal
from .scoring_backfill_contract import (
    SCORING_BACKFILL_PLAN_VERSION,
    backfill_contract_error as _backfill_contract_error,
    backfill_contract_error_code as _backfill_contract_error_code,
    normalize_scoring_backfill_plan,
    parsed_datetime as _parsed_datetime,
    positive_int as _positive_int,
    scoring_backfill_plan_digest,
    scoring_backfill_plan_from_counters,
    target_ids as _target_ids,
)
from .scoring_batch_fanout_recovery import (
    SCORING_QUEUE_CONTRACT,
    mark_scoring_fanout_publish_failed,
    mark_scoring_fanout_published,
    reserve_scoring_fanout_publish,
)
from .scoring_recovery_audit import (
    json_boolean_equals,
    json_boolean_false_or_missing,
    json_integer_equals,
    mark_recovery_audited,
    recovery_audit_due,
    recovery_audit_order,
)


logger = logging.getLogger(__name__)

SCORING_BACKFILL_ACTIVE_STATUSES = ("dispatching", "running")
_MAX_RECONCILE_LIMIT = 100
_BACKFILL_AUDIT_KEY = "backfill_recovery_audited_at"
_RECOVERY_AUDIT_SECONDS = 10 * 60


def _lease_due_filter(db: Session, *, current: str):
    lease = BackgroundJobRun.counters["fanout_lease_expires_at"].as_string()
    if db.get_bind().dialect.name == "postgresql":
        invalid = and_(
            lease.isnot(None),
            ~func.pg_input_is_valid(lease, "timestamp with time zone"),
        )
    else:
        invalid = and_(lease.isnot(None), func.datetime(lease).is_(None))
    return or_(lease.is_(None), lease <= current, invalid)


def _quarantine_parent(
    parent: BackgroundJobRun, counters: dict[str, Any], *, reason: str, error: str
) -> None:
    current = datetime.now(timezone.utc)
    counters.update(
        fanout_quarantined_at=current.isoformat(),
        fanout_quarantine_reason=reason,
    )
    parent.counters = counters
    parent.status = "failed"
    parent.error = error
    parent.finished_at = current


def scoring_backfill_child_counters(
    *,
    target_ids: list[int],
    include_scored: bool,
    applied_after: str | None,
    parent_run_id: int,
) -> dict[str, Any]:
    """Build the exact child receipt shared by initial and recovered fan-out."""

    return {
        "total": len(target_ids),
        "selected_total": len(target_ids),
        "target_application_ids": list(target_ids),
        "dispatched_application_ids": [],
        "score_job_ids": [],
        "owned_score_job_ids": [],
        "queue_contract": SCORING_QUEUE_CONTRACT,
        "backfill_parent_run_id": int(parent_run_id),
        "scored": 0,
        "errors": 0,
        "pre_screened_out": 0,
        "include_scored": bool(include_scored),
        "applied_after": applied_after,
        "fanout_state": "dispatching",
        "fanout_complete": False,
    }


def _children_by_role(
    counters: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    children: dict[int, dict[str, Any]] = {}
    raw_children = counters.get("children")
    if not isinstance(raw_children, list):
        return children
    for raw in raw_children:
        if not isinstance(raw, Mapping):
            continue
        role_id = _positive_int(raw.get("role_id"))
        run_id = _positive_int(raw.get("run_id"))
        if role_id is None or run_id is None or role_id in children:
            continue
        children[role_id] = dict(raw)
    return children


def scoring_backfill_fanout_accounted(counters: Mapping[str, Any]) -> bool:
    """Require a valid plan, completed cursor, and one child per planned role."""

    plan = scoring_backfill_plan_from_counters(counters)
    if plan is None or not bool(counters.get("fanout_complete")):
        return False
    children = _children_by_role(counters)
    planned_roles = {entry["role_id"] for entry in plan}
    cursor = counters.get("fanout_cursor")
    return (
        type(cursor) is int and cursor == len(plan) and planned_roles == set(children)
    )


def _exact_child(
    db: Session,
    *,
    parent: BackgroundJobRun,
    role_id: int,
) -> BackgroundJobRun | None:
    key = f"scoring-backfill:{int(parent.id)}:{role_id}"
    child = (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.dispatch_key == key)
        .one_or_none()
    )
    if child is None:
        return None
    if (
        child.kind != JOB_KIND_SCORING_BATCH
        or child.scope_kind != SCOPE_KIND_ROLE
        or int(child.scope_id) != role_id
        or int(child.organization_id) != int(parent.organization_id)
        or int(dict(child.counters or {}).get("backfill_parent_run_id") or 0)
        != int(parent.id)
    ):
        logger.error(
            "Scoring backfill child receipt scope mismatch parent_id=%s role_id=%s",
            parent.id,
            role_id,
        )
        raise ValueError("scoring backfill child receipt scope mismatch")
    return child


def _child_entry(
    *,
    parent: BackgroundJobRun,
    child: BackgroundJobRun,
    plan_entry: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "role_id": int(plan_entry["role_id"]),
        "target": len(plan_entry["target_application_ids"]),
        "run_id": int(child.id),
        "started_at": parent.started_at.isoformat(),
        "dispatch_status": (
            "dispatched" if child.status != "dispatching" else "recovery_pending"
        ),
    }


def _claim_next_child(
    db: Session,
    *,
    parent_run_id: int,
) -> tuple[dict[str, Any] | None, str]:
    parent = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(parent_run_id),
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ORG,
            BackgroundJobRun.finished_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if parent is None:
        return None, "not_active"
    contract_error = _backfill_contract_error(parent)
    counters = dict(parent.counters) if isinstance(parent.counters, dict) else {}
    if contract_error is not None:
        _quarantine_parent(
            parent,
            counters,
            reason=contract_error,
            error=_backfill_contract_error_code(contract_error),
        )
        return None, "invalid_contract"
    raw_lease = counters.get("fanout_lease_expires_at")
    lease = _parsed_datetime(raw_lease) if raw_lease is not None else None
    if lease is not None and lease > datetime.now(timezone.utc):
        return None, "leased"
    applied_after = counters.get("applied_after")
    if applied_after is not None and _parsed_datetime(applied_after) is None:
        applied_after = None
    plan = scoring_backfill_plan_from_counters(counters)
    assert plan is not None  # Validated by _backfill_contract_error above.

    children = _children_by_role(counters)
    next_payload: dict[str, Any] | None = None
    next_outcome = "accounted"
    for entry in plan:
        role_id = int(entry["role_id"])
        was_accounted = role_id in children
        try:
            child = _exact_child(db, parent=parent, role_id=role_id)
        except ValueError:
            parent.status = "failed"
            parent.error = "scoring_backfill_child_receipt_invalid"
            parent.finished_at = datetime.now(timezone.utc)
            return None, "invalid_child"
        if child is not None and _target_ids(
            dict(child.counters or {}).get("target_application_ids")
        ) != list(entry["target_application_ids"]):
            parent.status = "failed"
            parent.error = "scoring_backfill_child_targets_invalid"
            parent.finished_at = datetime.now(timezone.utc)
            return None, "invalid_child"
        if child is None:
            target_ids = list(entry["target_application_ids"])
            child = BackgroundJobRun(
                kind=JOB_KIND_SCORING_BATCH,
                scope_kind=SCOPE_KIND_ROLE,
                scope_id=role_id,
                organization_id=int(parent.organization_id),
                status="dispatching",
                counters=scoring_backfill_child_counters(
                    target_ids=target_ids,
                    include_scored=bool(counters.get("include_scored")),
                    applied_after=applied_after,
                    parent_run_id=int(parent.id),
                ),
                dispatch_key=f"scoring-backfill:{int(parent.id)}:{role_id}",
            )
            db.add(child)
            db.flush()
            next_outcome = "created"
        elif not was_accounted:
            next_outcome = "adopted"
        if child is not None and (next_outcome == "created" or not was_accounted):
            next_payload = {
                "run_id": int(child.id),
                "role_id": role_id,
                "organization_id": int(parent.organization_id),
                "include_scored": bool(counters.get("include_scored")),
                "applied_after": applied_after,
            }
        if child is not None:
            children[role_id] = _child_entry(
                parent=parent,
                child=child,
                plan_entry=entry,
            )
        if next_payload is not None:
            break

    ordered_children = [
        children[int(entry["role_id"])]
        for entry in plan
        if int(entry["role_id"]) in children
    ]
    contiguous = 0
    for entry in plan:
        if int(entry["role_id"]) not in children:
            break
        contiguous += 1
    fanout_complete = len(ordered_children) == len(plan)
    counters.update(
        children=ordered_children,
        fanout_cursor=contiguous,
        fanout_complete=fanout_complete,
    )
    counters.pop("fanout_lease_expires_at", None)
    parent.counters = counters
    if not plan:
        parent.status = "completed"
        parent.finished_at = datetime.now(timezone.utc)
    else:
        parent.status = "running"
    return next_payload, next_outcome


def _publish_child(payload: Mapping[str, Any]) -> str:
    scope = {
        "run_id": int(payload["run_id"]),
        "role_id": int(payload["role_id"]),
        "organization_id": int(payload["organization_id"]),
    }
    if reserve_scoring_fanout_publish(**scope) is None:
        return "already_reserved"
    try:
        from ..tasks.scoring_tasks import batch_score_role

        batch_score_role.delay(
            int(payload["role_id"]),
            include_scored=bool(payload["include_scored"]),
            applied_after=payload.get("applied_after"),
            run_id=int(payload["run_id"]),
        )
    except Exception as exc:
        logger.error(
            "Scoring backfill recovery publish failed run_id=%s error_type=%s",
            payload["run_id"],
            type(exc).__name__,
        )
        mark_scoring_fanout_publish_failed(**scope)
        return "publish_failed"
    mark_scoring_fanout_published(**scope)
    return "published"


def recover_scoring_backfill_parent(
    parent_run_id: int,
    *,
    max_children: int = 25,
) -> dict[str, int]:
    """Account and publish a bounded number of missing children for one parent."""

    budget = max(1, min(int(max_children), _MAX_RECONCILE_LIMIT))
    result = {"created": 0, "adopted": 0, "published": 0, "publish_failed": 0}
    for _ in range(budget):
        with SessionLocal() as db:
            payload, outcome = _claim_next_child(db, parent_run_id=parent_run_id)
            db.commit()
        if payload is None:
            break
        if outcome in {"created", "adopted"}:
            result[outcome] += 1
        publish_outcome = _publish_child(payload)
        if publish_outcome == "published":
            result["published"] += 1
        elif publish_outcome == "publish_failed":
            result["publish_failed"] += 1
        if outcome != "created":
            break
    return result


def reconcile_scoring_backfill_fanout(*, limit: int = 25) -> dict[str, int]:
    """Recover a bounded fleet of unfinished cross-role parent fan-outs."""

    budget = max(1, min(int(limit), _MAX_RECONCILE_LIMIT))
    now = datetime.now(timezone.utc)
    current = now.isoformat()
    audit_stale_before = (now - timedelta(seconds=_RECOVERY_AUDIT_SECONDS)).isoformat()
    with SessionLocal() as db:

        def _active_org_runs():
            return db.query(BackgroundJobRun).filter(
                BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ORG,
                BackgroundJobRun.status.in_(SCORING_BACKFILL_ACTIVE_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
            )

        # Do not trust JSON identity/version markers to select their own audit.
        # Persisting the audit timestamp makes the bounded id scan rotate: once
        # an old prefix is checked it moves behind every unaudited active row.
        audit_rows = (
            _active_org_runs()
            .filter(
                recovery_audit_due(
                    db,
                    _BACKFILL_AUDIT_KEY,
                    current=current,
                    stale_before=audit_stale_before,
                )
            )
            .order_by(*recovery_audit_order(db, _BACKFILL_AUDIT_KEY, current=current))
            .limit(_MAX_RECONCILE_LIMIT)
            .with_for_update(skip_locked=True)
            .all()
        )
        audited_ids: set[int] = set()
        quarantined_ids: set[int] = set()
        for parent in audit_rows:
            audited_ids.add(int(parent.id))
            contract_error = _backfill_contract_error(parent, now=now)
            counters = (
                dict(parent.counters) if isinstance(parent.counters, dict) else {}
            )
            if contract_error is not None:
                _quarantine_parent(
                    parent,
                    counters,
                    reason=contract_error,
                    error=_backfill_contract_error_code(contract_error),
                )
                quarantined_ids.add(int(parent.id))
                continue
            mark_recovery_audited(parent, _BACKFILL_AUDIT_KEY, now=now)
        db.commit()

        # The ordinary path remains narrow and due-only. Exact typed marker
        # predicates avoid database casts of corrupt JSON; the fair audit above
        # is responsible for quarantining anything those predicates reject.
        candidate_rows = (
            _active_org_runs()
            .filter(
                json_boolean_equals(db, "backfill_parent", True),
                json_integer_equals(
                    db,
                    "role_plan_version",
                    SCORING_BACKFILL_PLAN_VERSION,
                ),
                json_boolean_false_or_missing(db, "fanout_complete"),
                _lease_due_filter(db, current=current),
            )
            .order_by(BackgroundJobRun.id.asc())
            .limit(_MAX_RECONCILE_LIMIT)
            .all()
        )
        parent_ids: list[int] = []
        for parent in candidate_rows:
            parent_id = int(parent.id)
            audited_ids.add(parent_id)
            contract_error = _backfill_contract_error(parent, now=now)
            if contract_error is None:
                parent_ids.append(parent_id)
                continue
            counters = (
                dict(parent.counters) if isinstance(parent.counters, dict) else {}
            )
            _quarantine_parent(
                parent,
                counters,
                reason=contract_error,
                error=_backfill_contract_error_code(contract_error),
            )
            quarantined_ids.add(parent_id)
        db.commit()

    totals = {
        "parents_scanned": len(audited_ids),
        "parents_recovered": len(quarantined_ids),
        "parents_quarantined": len(quarantined_ids),
        "created": 0,
        "adopted": 0,
        "published": 0,
        "publish_failed": 0,
    }
    for parent_id in parent_ids:
        used = totals["created"] + totals["adopted"]
        if used >= budget:
            break
        recovered = recover_scoring_backfill_parent(
            parent_id,
            max_children=budget - used,
        )
        totals["parents_recovered"] += 1
        for key in ("created", "adopted", "published", "publish_failed"):
            totals[key] += recovered[key]
    return totals


__all__ = [
    "SCORING_BACKFILL_PLAN_VERSION",
    "normalize_scoring_backfill_plan",
    "reconcile_scoring_backfill_fanout",
    "recover_scoring_backfill_parent",
    "scoring_backfill_child_counters",
    "scoring_backfill_fanout_accounted",
    "scoring_backfill_plan_digest",
    "scoring_backfill_plan_from_counters",
]
