from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel
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


class _AdminClearSyncBody(BaseModel):
    email: str


@router.get("/admin/diagnostic")
def admin_workable_diagnostic(
    email: str = Query(..., description="User email (e.g. sampatel@deeplight.ae)"),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    """Run Workable API diagnostic for a user by email. Requires X-Admin-Secret header (SECRET_KEY)."""
    if not x_admin_secret or x_admin_secret.strip() != (settings.SECRET_KEY or "").strip():
        raise HTTPException(status_code=403, detail="Forbidden")
    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise HTTPException(status_code=400, detail="email required")
    user = db.query(User).filter(User.email == email_clean).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found: {email_clean}")
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    diagnostic = _run_workable_diagnostic(org)
    roles = (
        db.query(Role)
        .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
        .order_by(Role.created_at.desc())
        .limit(20)
        .all()
    )
    roles_summary = []
    for r in roles:
        app_count = db.query(CandidateApplication).filter(
            CandidateApplication.role_id == r.id,
            CandidateApplication.deleted_at.is_(None),
        ).count()
        roles_summary.append({
            "id": r.id,
            "name": (r.name or "")[:50],
            "workable_job_id": r.workable_job_id,
            "applications_count": app_count,
            "has_job_spec": bool((r.job_spec_text or r.description or "").strip()),
        })
    diagnostic["db_roles_count"] = len(roles)
    diagnostic["db_roles"] = roles_summary
    return diagnostic


@router.post("/admin/clear-sync")
def admin_clear_workable_sync(
    body: _AdminClearSyncBody,
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    """Clear Workable sync state for a user by email. Requires X-Admin-Secret header (SECRET_KEY)."""
    if not x_admin_secret or x_admin_secret.strip() != (settings.SECRET_KEY or "").strip():
        raise HTTPException(status_code=403, detail="Forbidden")
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found: {email}")
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    db.query(Organization).filter(Organization.id == org.id).update(
        {
            Organization.workable_sync_started_at: None,
            Organization.workable_sync_progress: None,
            Organization.workable_sync_cancel_requested_at: None,
        },
        synchronize_session=False,
    )
    db.commit()
    return {"status": "ok", "message": f"Cleared Workable sync state for {email}. They can start a new sync."}


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
        db.query(Organization).filter(Organization.id == org_id).update(
            {
                Organization.workable_sync_started_at: None,
                Organization.workable_sync_progress: None,
                Organization.workable_sync_cancel_requested_at: None,
            },
            synchronize_session=False,
        )
        db.commit()
        db.close()


def _run_workable_diagnostic(org: Organization) -> dict:
    """Run Workable API diagnostic for the org. Returns structured output for testing."""
    if not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        return {"error": "Workable not connected"}
    client = WorkableService(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result: dict = {
        "jobs": {"count": 0, "first_job_keys": [], "first_shortcode": None, "first_id": None, "first_title": None},
        "job_details": {"top_level_keys": [], "job_wrapper_keys": [], "details_keys": []},
        "candidates": {"count": 0, "first_candidate_keys": [], "first_email": None, "first_stage": None},
    }
    try:
        jobs = client.list_open_jobs()
        result["jobs"]["count"] = len(jobs)
        if jobs:
            j0 = jobs[0]
            result["jobs"]["first_job_keys"] = list(j0.keys())
            result["jobs"]["first_shortcode"] = j0.get("shortcode")
            result["jobs"]["first_id"] = j0.get("id")
            result["jobs"]["first_title"] = j0.get("title")

            shortcode = j0.get("shortcode") or j0.get("id")
            if shortcode:
                details = client.get_job_details(str(shortcode))
                if isinstance(details, dict):
                    result["job_details"]["top_level_keys"] = list(details.keys())
                    job_wrapped = details.get("job")
                    if isinstance(job_wrapped, dict):
                        result["job_details"]["job_wrapper_keys"] = list(job_wrapped.keys())[:20]
                        det = job_wrapped.get("details")
                        if isinstance(det, dict):
                            result["job_details"]["details_keys"] = list(det.keys())

                candidates = client.list_job_candidates(str(shortcode), paginate=True, max_pages=2)
                result["candidates"]["count"] = len(candidates)
                if candidates:
                    c0 = candidates[0]
                    result["candidates"]["first_candidate_keys"] = list(c0.keys())
                    result["candidates"]["first_email"] = c0.get("email")
                    result["candidates"]["first_stage"] = c0.get("stage") or c0.get("stage_name")
        result["api_reachable"] = True
    except Exception as exc:
        result["api_reachable"] = False
        result["error"] = str(exc)
        logger.exception("Workable diagnostic failed")
    return result


@router.get("/diagnostic")
def workable_diagnostic(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run Workable API diagnostic for current user's org. For testing integration."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    diagnostic = _run_workable_diagnostic(org)

    roles = (
        db.query(Role)
        .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
        .order_by(Role.created_at.desc())
        .limit(20)
        .all()
    )
    roles_summary = []
    for r in roles:
        app_count = db.query(CandidateApplication).filter(
            CandidateApplication.role_id == r.id,
            CandidateApplication.deleted_at.is_(None),
        ).count()
        roles_summary.append({
            "id": r.id,
            "name": (r.name or "")[:50],
            "workable_job_id": r.workable_job_id,
            "applications_count": app_count,
            "has_job_spec": bool((r.job_spec_text or r.description or "").strip()),
        })
    diagnostic["db_roles_count"] = len(roles)
    diagnostic["db_roles"] = roles_summary
    return diagnostic


@router.get("/sync/status")
def workable_sync_status(
    include_diagnostic: bool = Query(False, description="Include Workable API diagnostic for testing"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    started_at = org.workable_sync_started_at
    if started_at is not None:
        now = datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        age_seconds = (now - started_at).total_seconds()
        if age_seconds > 7200:  # 2 hours: treat as stale (crashed worker)
            db.query(Organization).filter(Organization.id == org.id).update(
                {Organization.workable_sync_started_at: None}, synchronize_session=False
            )
            db.commit()
            started_at = None
    sync_in_progress = started_at is not None
    db_roles_count = (
        db.query(Role)
        .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
        .count()
    )
    db_applications_count = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .count()
    )
    out = {
        "workable_connected": bool(org.workable_connected),
        "workable_last_sync_at": org.workable_last_sync_at,
        "workable_last_sync_status": org.workable_last_sync_status,
        "workable_last_sync_summary": org.workable_last_sync_summary or {},
        "workable_sync_progress": org.workable_sync_progress or {},
        "sync_in_progress": sync_in_progress,
        "db_roles_count": db_roles_count,
        "db_applications_count": db_applications_count,
    }
    if include_diagnostic:
        diag = _run_workable_diagnostic(org)
        out["diagnostic"] = diag
    return out


@router.post("/sync/cancel")
def cancel_workable_sync(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stop the current sync and clear state immediately so the UI shows not running.
    Sets cancel flag so the background thread stops at its next checkpoint (Workable API uses 30s timeout).
    """
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    if org.workable_sync_started_at is None:
        return {"status": "ok", "message": "No sync in progress."}
    now = datetime.now(timezone.utc)
    db.query(Organization).filter(Organization.id == org.id).update(
        {
            Organization.workable_sync_cancel_requested_at: now,
            Organization.workable_sync_started_at: None,
            Organization.workable_sync_progress: None,
        },
        synchronize_session=False,
    )
    db.commit()
    return {"status": "ok", "message": "Sync stopped. You can start a new sync when ready."}


@router.post("/sync")
def run_workable_sync(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)

    if org.workable_sync_started_at is not None:
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress. Check status below or try again in a few minutes.",
        )

    now = datetime.now(timezone.utc)
    org.workable_sync_started_at = now
    org.workable_sync_cancel_requested_at = None
    db.commit()

    thread = threading.Thread(target=_run_sync_in_background, args=(org.id,), daemon=True)
    thread.start()
    return {
        "status": "started",
        "message": "Sync started in the background. Poll /workable/sync/status to see progress.",
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
