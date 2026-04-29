"""Listing endpoint for the Settings → Background jobs panel.

Returns recent rows from ``background_job_runs`` for the caller's
organization, joined with ``roles.name`` for per-role rows so the
frontend can render the scope label without a second lookup.

The Workable sync history lives in its own table — see
``GET /workable/sync/runs`` for that.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.background_job_run import BackgroundJobRun, SCOPE_KIND_ROLE
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(prefix="/background-jobs", tags=["Background jobs"])


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


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

    return {
        "runs": [
            {
                "id": r.id,
                "kind": r.kind,
                "scope_kind": r.scope_kind,
                "scope_id": r.scope_id,
                "role_name": role_names.get(int(r.scope_id)) if r.scope_kind == SCOPE_KIND_ROLE else None,
                "status": r.status,
                "counters": dict(r.counters or {}),
                "error": r.error,
                "started_at": _iso(r.started_at),
                "finished_at": _iso(r.finished_at),
                "cancel_requested_at": _iso(r.cancel_requested_at),
            }
            for r in rows
        ]
    }
