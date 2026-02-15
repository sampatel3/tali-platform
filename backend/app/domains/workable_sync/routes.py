from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...components.integrations.workable.service import WorkableService
from ...components.integrations.workable.sync_service import WorkableSyncService
from ...deps import get_current_user
from ...models.organization import Organization
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db

router = APIRouter(prefix="/workable", tags=["Workable"])


class WorkableSyncRequest(BaseModel):
    full_resync: bool = False


def _get_org_for_user(db: Session, current_user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _assert_workable_connected(org: Organization) -> None:
    if not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        raise HTTPException(status_code=400, detail="Workable is not connected")


@router.get("/sync/status")
def workable_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    return {
        "workable_connected": bool(org.workable_connected),
        "workable_last_sync_at": org.workable_last_sync_at,
        "workable_last_sync_status": org.workable_last_sync_status,
        "workable_last_sync_summary": org.workable_last_sync_summary or {},
    }


@router.post("/sync")
def run_workable_sync(
    body: WorkableSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)

    service = WorkableSyncService(
        WorkableService(
            access_token=org.workable_access_token,
            subdomain=org.workable_subdomain,
        )
    )
    try:
        summary = service.sync_org(db, org, full_resync=bool(body.full_resync))
        return {
            "status": "ok",
            "workable_last_sync_at": org.workable_last_sync_at,
            "workable_last_sync_status": org.workable_last_sync_status,
            "summary": summary,
        }
    except Exception:
        raise HTTPException(status_code=502, detail="Workable sync failed")
