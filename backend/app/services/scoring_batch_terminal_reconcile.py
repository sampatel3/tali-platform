"""Provider-free terminal reconciliation for drained scoring batches."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import exists

from ..domains.assessments_runtime.scoring_batch_state import (
    scoring_batch_exact_terminal_breakdown,
    scoring_uses_exact_receipts,
)
from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..models.cv_score_job import SCORE_JOB_PENDING, SCORE_JOB_RUNNING, CvScoreJob
from ..platform.database import SessionLocal
from .scoring_batch_terminal_contract import exact_scoring_terminal_counts
from .scoring_batch_successors import QUEUE_CONTRACT


_ACTIVE_STATUSES = ("dispatching", "queued", "running", "cancelling")
_SCAN_FLOOR = 100
_SCAN_CAP = 1_000
_INCOMPLETE_RECEIPTS_ERROR = "scoring_batch_incomplete_terminal_receipts"
_INVALID_RECEIPTS_ERROR = "scoring_batch_invalid_terminal_receipts"


def _terminal_status(
    db,
    run: BackgroundJobRun,
) -> tuple[str | None, dict, str | None]:
    counters = dict(run.counters) if isinstance(run.counters, dict) else {}
    if not scoring_uses_exact_receipts(counters):
        return "failed", counters, _INVALID_RECEIPTS_ERROR
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
        return "failed", counters, _INVALID_RECEIPTS_ERROR
    raw_dispatched_ids = counters.get("dispatched_application_ids")
    if not isinstance(raw_dispatched_ids, list) or any(
        type(value) is not int or value <= 0 for value in raw_dispatched_ids
    ):
        return "failed", counters, _INVALID_RECEIPTS_ERROR
    dispatched_ids = frozenset(raw_dispatched_ids)
    observed_ids = breakdown.terminal_application_ids | breakdown.active_application_ids
    if not observed_ids <= dispatched_ids:
        return "failed", counters, _INVALID_RECEIPTS_ERROR
    if breakdown.active_application_ids:
        return None, counters, None
    if dispatched_ids != breakdown.terminal_application_ids:
        return "failed", counters, _INCOMPLETE_RECEIPTS_ERROR
    cancelled = str(run.status) == "cancelling" or run.cancel_requested_at is not None
    final_errors = breakdown.errors if cancelled else exact.errors + exact.not_enqueued
    counters.update(
        scored=exact.scored,
        errors=final_errors,
        pre_screened_out=exact.pre_screened_out,
        not_processed=(breakdown.cancelled + exact.not_enqueued if cancelled else 0),
    )
    if exact.accounted != exact.target_total:
        return "failed", counters, _INCOMPLETE_RECEIPTS_ERROR
    if cancelled:
        return "cancelled", counters, None
    if counters.get("fanout_failed") is True:
        return "failed", counters, str(run.error or "scoring_batch_fanout_failed")
    return "completed", counters, None


def reconcile_drained_scoring_batches(*, limit: int = 25) -> dict[str, int]:
    """Terminalize a bounded number of exact batches without browser polling."""

    bounded_limit = max(1, min(limit, 100)) if type(limit) is int else 25
    scan_limit = min(_SCAN_CAP, max(_SCAN_FLOOR, bounded_limit * 4))
    result = {
        "examined": 0,
        "active": 0,
        "completed": 0,
        "cancelled": 0,
        "failed": 0,
    }
    active_owned_receipt = exists().where(
        CvScoreJob.batch_run_id == BackgroundJobRun.id,
        CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
    )
    with SessionLocal() as db:
        rows = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.status.in_(_ACTIVE_STATUSES),
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.counters["queue_contract"].as_string()
                == QUEUE_CONTRACT,
                BackgroundJobRun.counters["fanout_complete"].as_boolean().is_(True),
            )
            .order_by(active_owned_receipt.asc(), BackgroundJobRun.id.asc())
            .limit(scan_limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        terminalized = 0
        for run in rows:
            result["examined"] += 1
            status, counters, error = _terminal_status(db, run)
            if status is None:
                result["active"] += 1
                continue
            run.status = status
            run.counters = counters
            run.finished_at = datetime.now(timezone.utc)
            if error is not None:
                run.error = error
            result[status] += 1
            terminalized += 1
            if terminalized >= bounded_limit:
                break
        db.commit()
    return result


__all__ = ["reconcile_drained_scoring_batches"]
