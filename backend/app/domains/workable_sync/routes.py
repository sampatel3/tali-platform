from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...components.integrations.workable.service import WorkableService
from ...components.integrations.workable.sync_service import WorkableSyncService
from ...deps import get_current_user
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.config import settings
from ...platform.database import SessionLocal, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workable", tags=["Workable"])

# In-memory set of org_ids currently running a background sync (single process).
_workable_sync_in_progress: set[int] = set()
_lock = threading.Lock()


def _get_org_for_user(db: Session, current_user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _assert_workable_connected(org: Organization) -> None:
    if not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        raise HTTPException(status_code=400, detail="Workable is not connected")


def _run_sync_in_background(org_id: int) -> None:
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org or not org.workable_access_token or not org.workable_subdomain:
            return
        service = WorkableSyncService(
            WorkableService(
                access_token=org.workable_access_token,
                subdomain=org.workable_subdomain,
            )
        )
        service.sync_org(db, org)
    except Exception as exc:
        logger.exception("Workable background sync failed for org_id=%s: %s", org_id, exc)
    finally:
        with _lock:
            _workable_sync_in_progress.discard(org_id)
        db.close()


@router.get("/sync/status")
def workable_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    with _lock:
        sync_in_progress = org.id in _workable_sync_in_progress
    return {
        "workable_connected": bool(org.workable_connected),
        "workable_last_sync_at": org.workable_last_sync_at,
        "workable_last_sync_status": org.workable_last_sync_status,
        "workable_last_sync_summary": org.workable_last_sync_summary or {},
        "sync_in_progress": sync_in_progress,
    }


@router.post("/sync")
def run_workable_sync(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)

    with _lock:
        if org.id in _workable_sync_in_progress:
            raise HTTPException(
                status_code=409,
                detail="A sync is already in progress. Check status below or try again in a few minutes.",
            )
        _workable_sync_in_progress.add(org.id)

    thread = threading.Thread(target=_run_sync_in_background, args=(org.id,), daemon=True)
    thread.start()
    return {
        "status": "started",
        "message": "Sync started in the background. This may take several minutes due to API rate limits. Poll /workable/sync/status or refresh this page to see progress.",
    }


@router.post("/clear")
def clear_workable_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete all Workable-imported roles, applications, and candidates for this org.
    Records are marked with deleted_at; they are not physically removed.
    """
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    org_id = current_user.organization_id
    now = datetime.now(timezone.utc)

    roles_updated = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.source == "workable", Role.deleted_at.is_(None))
        .update({Role.deleted_at: now}, synchronize_session=False)
    )
    apps_updated = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.source == "workable",
            CandidateApplication.deleted_at.is_(None),
        )
        .update({CandidateApplication.deleted_at: now}, synchronize_session=False)
    )
    candidates_updated = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org_id,
            Candidate.workable_candidate_id.isnot(None),
            Candidate.deleted_at.is_(None),
        )
        .update({Candidate.deleted_at: now}, synchronize_session=False)
    )

    db.commit()
    return {
        "status": "ok",
        "roles_soft_deleted": roles_updated,
        "applications_soft_deleted": apps_updated,
        "candidates_soft_deleted": candidates_updated,
    }
