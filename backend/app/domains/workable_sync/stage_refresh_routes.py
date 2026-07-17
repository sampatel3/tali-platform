"""Targeted, transaction-detached Workable candidate-stage refresh route."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.document_service import sanitize_text_for_storage
from .provider_reads import (
    assert_workable_connected,
    get_org_for_user,
    release_for_workable_provider,
    workable_client_snapshot,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class StageRefreshResult(BaseModel):
    job_linked: bool
    checked: int
    updated: int
    message: str


@router.post("/roles/{role_id}/refresh-stages", response_model=StageRefreshResult)
def refresh_role_workable_stages(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refresh one role's local stage cache from its current Workable job."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(
            status_code=503,
            detail="Workable integration is disabled for MVP",
        )
    org = get_org_for_user(db, current_user)
    assert_workable_connected(org)
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    if not role.workable_job_id:
        return StageRefreshResult(
            job_linked=False,
            checked=0,
            updated=0,
            message=(
                "This role isn't linked to a Workable job, so there are no "
                "stages to sync."
            ),
        )

    org_id = int(org.id)
    selected_role_id = int(role.id)
    workable_job_id = str(role.workable_job_id)
    client = workable_client_snapshot(org)
    if client is None:
        raise HTTPException(status_code=409, detail="Workable connection is incomplete")
    release_for_workable_provider(db)
    try:
        candidates = client.list_job_candidates(workable_job_id, paginate=True)
    except Exception as exc:
        logger.exception(
            "Failed refreshing Workable stages for role_id=%s: %s",
            selected_role_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to refresh candidate stages from Workable.",
        ) from exc

    stage_by_id = {
        str(candidate.get("id") or "").strip(): str(candidate.get("stage"))
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("id") or "").strip()
        and candidate.get("stage")
    }
    current_role = require_job_permission(
        db,
        current_user=current_user,
        role_id=selected_role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    if str(current_role.workable_job_id or "") != workable_job_id:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The role's Workable job link changed during the refresh.",
        )

    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == selected_role_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.workable_candidate_id.isnot(None),
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    updated = 0
    for app in apps:
        live = stage_by_id.get(str(app.workable_candidate_id))
        if live and live != (app.workable_stage or ""):
            app.workable_stage = sanitize_text_for_storage(live)
            updated += 1
    if updated:
        db.commit()
    return StageRefreshResult(
        job_linked=True,
        checked=len(apps),
        updated=updated,
        message=(
            f"Synced {updated} stage change{'s' if updated != 1 else ''} from Workable."
            if updated
            else "All stages already match Workable."
        ),
    )


__all__ = ["StageRefreshResult", "refresh_role_workable_stages", "router"]
