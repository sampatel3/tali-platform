"""Provider-free dispatch and bounded recovery for scoring successors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, exists, or_, text

from ..domains.assessments_runtime.scoring_batch_state import (
    scoring_batch_exact_terminal_breakdown,
    scoring_batch_has_active_jobs,
    scoring_uses_exact_receipts,
)
from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..models.cv_score_job import SCORE_JOB_PENDING, SCORE_JOB_RUNNING, CvScoreJob
from ..platform.database import SessionLocal
from .background_job_runs import update_run
from .scoring_batch_successor_dispatch import (
    dispatch_claimed_scoring_successor,
    scoring_successor_target_ids,
)
from .scoring_batch_successor_contract import (
    scoring_successor_contract_error,
    scoring_successor_reconcile_after,
)
from .scoring_batch_terminal_contract import (
    exact_scoring_terminal_counts,
    exact_scoring_terminal_identity_error,
    resolve_exact_scoring_terminal_state,
)
from .scoring_batch_successors import (
    SUCCESSOR_KEY,
    claim_scoring_successor,
    release_scoring_successor,
)
from .scoring_recovery_audit import (
    json_boolean_equals,
    json_key_exists,
    mark_recovery_audited,
    recovery_audit_order,
)


_START_ADVISORY_NAMESPACE = 0x54414C49
_RECOVERY_SCAN_FLOOR = 100
_RECOVERY_SCAN_CAP = 1_000
_RECOVERY_DEFER_SECONDS = 60
_INCOMPLETE_RECEIPTS_ERROR = "scoring_batch_incomplete_terminal_receipts"
_INVALID_RECEIPTS_ERROR = "scoring_batch_invalid_terminal_receipts"
_SUCCESSOR_AUDIT_KEY = "successor_recovery_audited_at"


def _lock_role_scope(db, role_id: int) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :role_id)"),
            {"namespace": _START_ADVISORY_NAMESPACE, "role_id": int(role_id)},
        )


def _run_counters(run: BackgroundJobRun) -> dict[str, Any]:
    return dict(run.counters) if isinstance(run.counters, dict) else {}


def _has_active_receipts(db, run: BackgroundJobRun) -> bool:
    return scoring_batch_has_active_jobs(
        db,
        run_id=int(run.id),
        progress=_run_counters(run),
    )


def _ready_terminal_state(
    db,
    run: BackgroundJobRun,
) -> tuple[str | None, str | None, bool]:
    stored_status = str(run.status)
    counters = _run_counters(run)
    if (
        stored_status not in {"completed", "failed"}
        and counters.get("fanout_complete") is not True
    ):
        return None, None, _has_active_receipts(db, run)
    if not scoring_uses_exact_receipts(counters):
        return "failed", _INVALID_RECEIPTS_ERROR, False
    breakdown = scoring_batch_exact_terminal_breakdown(
        db,
        run_id=int(run.id),
        progress=counters,
    )
    exact = exact_scoring_terminal_counts(
        counters,
        scored=breakdown.scored,
        errors=breakdown.errors + breakdown.cancelled,
        pre_screened_out=breakdown.pre_screened_out,
    )
    if exact is None:
        return "failed", _INVALID_RECEIPTS_ERROR, False
    active = bool(breakdown.active_application_ids)
    identity_error = exact_scoring_terminal_identity_error(
        counters,
        terminal_application_ids=breakdown.terminal_application_ids,
        active_application_ids=breakdown.active_application_ids,
        drained=not active,
    )
    if identity_error is not None:
        return "failed", identity_error, active
    if active:
        return None, None, True
    status, error = resolve_exact_scoring_terminal_state(
        counters,
        stored_status=stored_status,
        scored=breakdown.scored,
        errors=breakdown.errors + breakdown.cancelled,
        pre_screened_out=breakdown.pre_screened_out,
    )
    return status, error, False


def _mutate_reconcile_candidate(
    db,
    parent: BackgroundJobRun,
    *,
    quarantine_reason: str | None = None,
    defer: bool = True,
) -> bool:
    """Defer a live candidate or quarantine an invalid one under row lock."""

    locked = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(parent.id),
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(parent.scope_id),
            BackgroundJobRun.organization_id == int(parent.organization_id),
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked is None:
        return False
    counters = _run_counters(locked)
    payload = counters.get(SUCCESSOR_KEY)
    contract_error = scoring_successor_contract_error(
        payload,
        role_id=int(locked.scope_id),
        organization_id=int(locked.organization_id),
    )
    now = datetime.now(timezone.utc)
    if quarantine_reason is None:
        if contract_error is not None:
            return False
        if defer:
            payload = dict(payload)
            payload["reconcile_after"] = (
                now + timedelta(seconds=_RECOVERY_DEFER_SECONDS)
            ).isoformat()
            counters[SUCCESSOR_KEY] = payload
        locked.counters = counters
        mark_recovery_audited(locked, _SUCCESSOR_AUDIT_KEY, now=now)
        counters = dict(locked.counters or {})
    else:
        safe_status, terminal_error, _active = _ready_terminal_state(db, locked)
        if quarantine_reason not in {contract_error, terminal_error}:
            return False
        counters["quarantined_scoring_successor"] = {
            "payload": payload,
            "reason": quarantine_reason,
            "quarantined_at": now.isoformat(),
        }
        counters.pop(SUCCESSOR_KEY, None)
        should_update_terminal = safe_status is not None and (
            str(locked.status) not in {"completed", "failed"}
            or terminal_error is not None
        )
        if should_update_terminal:
            locked.status = safe_status
            locked.finished_at = now
            if safe_status == "failed":
                locked.error = terminal_error or _INCOMPLETE_RECEIPTS_ERROR
    locked.counters = counters
    db.commit()
    return True


def reconcile_queued_scoring_successors(limit: int = 25) -> dict[str, int]:
    """Start terminal, drained intents without relying on browser polling."""

    bounded_limit = max(1, min(limit, 100)) if type(limit) is int else 25
    scan_limit = min(_RECOVERY_SCAN_CAP, max(_RECOVERY_SCAN_FLOOR, bounded_limit * 4))
    counts = {
        "examined": 0,
        "started": 0,
        "deduplicated": 0,
        "no_targets": 0,
        "invalid": 0,
        "quarantined": 0,
        "released": 0,
        "revoked": 0,
        "recovery_pending": 0,
        "deferred_active": 0,
        "deferred_not_ready": 0,
    }
    db = SessionLocal()
    try:
        current = datetime.now(timezone.utc).isoformat()
        reconcile_after = BackgroundJobRun.counters[SUCCESSOR_KEY][
            "reconcile_after"
        ].as_string()
        active_owned_receipt = exists().where(
            CvScoreJob.batch_run_id == BackgroundJobRun.id,
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
        )
        due_reconcile = or_(
            reconcile_after.is_(None),
            reconcile_after <= current,
        )
        audit_order = recovery_audit_order(
            db,
            _SUCCESSOR_AUDIT_KEY,
            current=current,
        )
        parents = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.status.in_(
                    (
                        "dispatching",
                        "queued",
                        "running",
                        "completed",
                        "failed",
                    )
                ),
                json_key_exists(db, SUCCESSOR_KEY),
                or_(
                    BackgroundJobRun.status.in_(("completed", "failed")),
                    json_boolean_equals(db, "fanout_complete", True),
                ),
            )
            # Rotate a bounded audit tail so malformed future metadata is visible.
            .order_by(
                case((due_reconcile, 0), else_=1),
                active_owned_receipt.asc(),
                *audit_order,
            )
            .limit(scan_limit)
            .all()
        )
        dispatches = 0
        for parent in parents:
            counts["examined"] += 1
            _lock_role_scope(db, int(parent.scope_id))
            db.refresh(parent)
            (
                terminal_status,
                terminal_error,
                has_active_receipts,
            ) = _ready_terminal_state(db, parent)
            counters = _run_counters(parent)
            contract_error = scoring_successor_contract_error(
                counters.get(SUCCESSOR_KEY),
                role_id=int(parent.scope_id),
                organization_id=int(parent.organization_id),
            )
            if contract_error is not None:
                if _mutate_reconcile_candidate(
                    db,
                    parent,
                    quarantine_reason=contract_error,
                ):
                    counts["quarantined"] += 1
                continue
            if terminal_error is not None:
                if _mutate_reconcile_candidate(
                    db,
                    parent,
                    quarantine_reason=terminal_error,
                ):
                    counts["quarantined"] += 1
                continue
            reconcile_at = scoring_successor_reconcile_after(
                counters.get(SUCCESSOR_KEY)
            )
            if reconcile_at is not None and reconcile_at > datetime.now(timezone.utc):
                counts["deferred_not_ready"] += 1
                _mutate_reconcile_candidate(db, parent, defer=False)
                continue
            if has_active_receipts:
                counts["deferred_active"] += 1
                _mutate_reconcile_candidate(db, parent)
                continue
            if terminal_status is None:
                counts["deferred_not_ready"] += 1
                _mutate_reconcile_candidate(db, parent, defer=False)
                continue
            if dispatches >= bounded_limit:
                break
            claimed = claim_scoring_successor(
                int(parent.id),
                role_id=int(parent.scope_id),
                organization_id=int(parent.organization_id),
            )
            if claimed is None:
                _mutate_reconcile_candidate(db, parent)
                continue
            dispatches += 1
            if str(parent.status) not in {"completed", "failed"}:
                failed_for_missing_receipts = (
                    terminal_status == "failed"
                    and counters.get("fanout_failed") is not True
                )
                if not update_run(
                    int(parent.id),
                    status=terminal_status,
                    error=(
                        _INCOMPLETE_RECEIPTS_ERROR
                        if failed_for_missing_receipts
                        else None
                    ),
                    finished=True,
                ):
                    release_scoring_successor(
                        int(parent.id),
                        role_id=int(parent.scope_id),
                        organization_id=int(parent.organization_id),
                        queue_id=str(claimed.get("queue_id") or ""),
                        claim_token=str(claimed.get("claim_token") or ""),
                    )
                    counts["released"] += 1
                    continue
            result = dispatch_claimed_scoring_successor(
                db,
                parent_run_id=int(parent.id),
                role_id=int(parent.scope_id),
                organization_id=int(parent.organization_id),
                claimed=claimed,
            )
            outcome = str(result.get("outcome") or "released")
            if outcome in counts:
                counts[outcome] += 1
        return counts
    finally:
        db.close()


__all__ = [
    "dispatch_claimed_scoring_successor",
    "reconcile_queued_scoring_successors",
    "scoring_successor_target_ids",
]
