"""Listing endpoint for the Settings → Background jobs panel.

Returns recent rows from ``background_job_runs`` for the caller's
organization, joined with ``roles.name`` for per-role rows so the
frontend can render the scope label without a second lookup.

The Workable sync history lives in its own table — see
``GET /workable/sync/runs`` for that.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.background_job_run import BackgroundJobRun, SCOPE_KIND_ROLE
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(prefix="/background-jobs", tags=["Background jobs"])

_PUBLIC_FAILURES = {
    "scoring_batch": (
        "scoring_batch_failed",
        "The scoring batch could not complete. Retry the failed candidates.",
    ),
    "cv_fetch": (
        "cv_fetch_failed",
        "The CV fetch could not complete. Check the ATS connection and retry.",
    ),
    "graph_sync": (
        "graph_sync_failed",
        "The talent-data sync could not complete. Retry when the service recovers.",
    ),
    "process_role": (
        "process_role_failed",
        "Candidate processing stopped before it completed. Review the saved progress and retry.",
    ),
    "decision_batch": (
        "decision_batch_failed",
        "The approval batch could not complete; unresolved decisions were returned to the queue.",
    ),
    "workable_op": (
        "ats_update_failed",
        "The ATS update could not complete. Check the connection and retry.",
    ),
}
_PUBLIC_ATS_CODES = frozenset(
    {
        "api_error",
        "delivery_lost",
        "initial_queue_unavailable",
        "lock_timeout",
        "lock_wait_queue_unavailable",
        "not_configured",
        "not_writeable",
        "rate_limited",
        "stale_delivery",
        "unexpected",
    }
)


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _public_counters(value) -> dict:
    """Strip encrypted/internal replay state from recruiter-facing telemetry."""

    counters = dict(value or {})
    counters.pop("recovery_payload", None)
    progress = counters.pop("progress", None)
    if isinstance(progress, dict):
        counters = dict(progress)
    for key in ("error", "error_message", "last_error", "traceback"):
        counters.pop(key, None)
    if isinstance(counters.get("errors"), list):
        counters["errors"] = len(counters["errors"])
    return counters


def _public_failure(row: BackgroundJobRun) -> tuple[str | None, str | None]:
    if not row.error:
        return None, None
    default_code, message = _PUBLIC_FAILURES.get(
        str(row.kind or ""),
        ("background_job_failed", "The background job could not complete. Retry the operation."),
    )
    counters = row.counters if isinstance(row.counters, dict) else {}
    candidate = str(counters.get("failure_code") or counters.get("error_code") or "")
    safe_code = candidate if candidate in _PUBLIC_ATS_CODES else default_code
    return safe_code or default_code, message


@router.get("/runs")
def list_background_job_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the N most recent background job runs for the caller's org.

    Covers scoring batch, CV fetch, and graph sync. Workable sync has its
    own listing endpoint at ``/workable/sync/runs``.
    """
    rows = (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.organization_id == current_user.organization_id)
        .order_by(BackgroundJobRun.id.desc())
        .limit(limit)
        .all()
    )

    role_ids = {r.scope_id for r in rows if r.scope_kind == SCOPE_KIND_ROLE}
    role_names: dict[int, str] = {}
    if role_ids:
        for rid, name in (
            db.query(Role.id, Role.name)
            .filter(Role.id.in_(role_ids))
            .all()
        ):
            role_names[int(rid)] = str(name or "")

    runs = []
    for r in rows:
        error_code, error_message = _public_failure(r)
        runs.append(
            {
                "id": r.id,
                "kind": r.kind,
                "scope_kind": r.scope_kind,
                "scope_id": r.scope_id,
                "role_name": role_names.get(int(r.scope_id)) if r.scope_kind == SCOPE_KIND_ROLE else None,
                "status": r.status,
                "counters": _public_counters(r.counters),
                "error": error_message,
                "error_code": error_code,
                "started_at": _iso(r.started_at),
                "finished_at": _iso(r.finished_at),
                "cancel_requested_at": _iso(r.cancel_requested_at),
            }
        )
    return {
        "runs": runs
    }


@router.get("/runs/{run_id}")
def get_background_job_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return one tracked run, scoped strictly to the caller's workspace."""

    row = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(run_id),
            BackgroundJobRun.organization_id == current_user.organization_id,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Background job not found")
    error_code, error_message = _public_failure(row)
    return {
        "id": row.id,
        "kind": row.kind,
        "scope_kind": row.scope_kind,
        "scope_id": row.scope_id,
        "status": row.status,
        "counters": _public_counters(row.counters),
        "error": error_message,
        "error_code": error_code,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "cancel_requested_at": _iso(row.cancel_requested_at),
    }
