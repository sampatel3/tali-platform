from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...components.integrations.workable.sync_runner import execute_workable_sync_run
from ...components.integrations.workable.service import WorkableService
from ...deps import get_current_user
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...models.workable_sync_run import WorkableSyncRun
from ...platform.config import settings
from ...platform.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workable", tags=["Workable"])


class _AdminClearSyncBody(BaseModel):
    email: str


class _SyncRequestBody(BaseModel):
    mode: Literal["metadata", "full"] = "metadata"
    # Legacy compatibility: frontend may still send skip_cv.
    skip_cv: bool | None = None
    # Optional list of Workable job shortcodes/IDs to limit sync scope.
    job_shortcodes: list[str] | None = None


class _SyncCancelBody(BaseModel):
    run_id: int | None = None


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


def _latest_run_for_org(db: Session, org_id: int) -> WorkableSyncRun | None:
    return (
        db.query(WorkableSyncRun)
        .filter(WorkableSyncRun.organization_id == org_id)
        .order_by(WorkableSyncRun.id.desc())
        .first()
    )


def _latest_running_run_for_org(db: Session, org_id: int) -> WorkableSyncRun | None:
    return (
        db.query(WorkableSyncRun)
        .filter(
            WorkableSyncRun.organization_id == org_id,
            WorkableSyncRun.finished_at.is_(None),
            WorkableSyncRun.status == "running",
        )
        .order_by(WorkableSyncRun.id.desc())
        .first()
    )


def _db_snapshot_for_org(db: Session, org_id: int) -> dict:
    return {
        "roles_active": (
            db.query(Role)
            .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
            .count()
        ),
        "applications_active": (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .count()
        ),
        "candidates_active": (
            db.query(Candidate)
            .filter(Candidate.organization_id == org_id, Candidate.deleted_at.is_(None))
            .count()
        ),
    }


def _run_payload(run: WorkableSyncRun | None, db_snapshot: dict) -> dict:
    def _iso(value):
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    if not run:
        return {
            "run_id": None,
            "phase": None,
            "jobs_total": 0,
            "jobs_processed": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "errors": [],
            "started_at": None,
            "finished_at": None,
            "cancel_requested_at": None,
            "mode": "metadata",
            "status": "idle",
            "db_snapshot": db_snapshot,
        }
    return {
        "run_id": run.id,
        "phase": run.phase,
        "jobs_total": run.jobs_total or 0,
        "jobs_processed": run.jobs_processed or 0,
        "candidates_seen": run.candidates_seen or 0,
        "candidates_upserted": run.candidates_upserted or 0,
        "applications_upserted": run.applications_upserted or 0,
        "errors": run.errors or [],
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "cancel_requested_at": _iso(run.cancel_requested_at),
        "mode": run.mode or "metadata",
        "status": run.status or "running",
        "db_snapshot": run.db_snapshot or db_snapshot,
    }


def _normalize_selected_job_shortcodes(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        # Defensive cap to avoid oversized payloads.
        if len(value) > 120:
            value = value[:120]
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _enqueue_sync(
    org_id: int,
    run_id: int,
    mode: str,
    selected_job_shortcodes: list[str] | None = None,
) -> str:
    """Queue sync in Celery when enabled; fallback to local thread otherwise."""
    if not settings.MVP_DISABLE_CELERY:
        try:
            from ...tasks.workable_tasks import run_workable_sync_run_task

            run_workable_sync_run_task.delay(
                org_id=org_id,
                run_id=run_id,
                mode=mode,
                selected_job_shortcodes=selected_job_shortcodes or None,
            )
            return "celery"
        except Exception as exc:
            logger.exception(
                "Failed to enqueue Workable sync in Celery for org_id=%s run_id=%s; falling back to thread: %s",
                org_id,
                run_id,
                exc,
            )

    thread = threading.Thread(
        target=_run_sync_in_background,
        args=(org_id, run_id, mode, selected_job_shortcodes),
        daemon=True,
    )
    thread.start()
    return "thread"


def _run_sync_in_background(
    org_id: int,
    run_id: int,
    mode: str,
    selected_job_shortcodes: list[str] | None = None,
) -> None:
    execute_workable_sync_run(
        org_id=org_id,
        run_id=run_id,
        mode=mode,
        selected_job_shortcodes=selected_job_shortcodes,
    )


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


@router.get("/sync/jobs")
def workable_sync_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List selectable Workable jobs for scoped metadata sync."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)
    client = WorkableService(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    try:
        jobs = client.list_open_jobs()
    except Exception as exc:
        logger.exception("Failed listing Workable jobs for org_id=%s: %s", org.id, exc)
        raise HTTPException(status_code=502, detail="Failed to load Workable roles from Workable API.")
    out: list[dict] = []
    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        shortcode = str(job.get("shortcode") or "").strip() or None
        job_id = str(job.get("id") or "").strip() or None
        identifier = shortcode or job_id
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        title = str(job.get("title") or job.get("name") or f"Workable role {identifier}").strip()
        state = str(job.get("state") or "").strip() or None
        out.append(
            {
                "shortcode": shortcode,
                "id": job_id,
                "identifier": identifier,
                "title": title,
                "state": state,
            }
        )
    out.sort(key=lambda row: (str(row.get("title") or "").lower(), str(row.get("identifier") or "")))
    return {"total": len(out), "jobs": out}


@router.get("/sync/status")
def workable_sync_status(
    run_id: int | None = Query(None, description="Optional sync run ID. Uses latest run when omitted."),
    include_diagnostic: bool = Query(False, description="Include Workable API diagnostic for testing"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)

    run = None
    if run_id is not None:
        run = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.id == run_id,
                WorkableSyncRun.organization_id == org.id,
            )
            .first()
        )
    if run is None:
        run = _latest_run_for_org(db, org.id)

    snapshot = _db_snapshot_for_org(db, org.id)
    run_payload = _run_payload(run, snapshot)
    sync_in_progress = bool(run and run.status == "running" and run.finished_at is None)

    out = {
        "workable_connected": bool(org.workable_connected),
        "active_claude_model": settings.active_claude_model,
        "active_claude_scoring_model": settings.active_claude_scoring_model,
        "workable_last_sync_at": org.workable_last_sync_at,
        "workable_last_sync_status": org.workable_last_sync_status,
        "workable_last_sync_summary": org.workable_last_sync_summary or {},
        "workable_sync_progress": org.workable_sync_progress or run_payload,
        "sync_in_progress": sync_in_progress,
        "db_roles_count": snapshot["roles_active"],
        "db_applications_count": snapshot["applications_active"],
        "run_id": run_payload["run_id"],
        "mode": run_payload["mode"],
        "phase": run_payload["phase"],
        "jobs_total": run_payload["jobs_total"],
        "jobs_processed": run_payload["jobs_processed"],
        "candidates_seen": run_payload["candidates_seen"],
        "candidates_upserted": run_payload["candidates_upserted"],
        "applications_upserted": run_payload["applications_upserted"],
        "errors": run_payload["errors"],
        "started_at": run_payload["started_at"],
        "finished_at": run_payload["finished_at"],
        "cancel_requested_at": run_payload["cancel_requested_at"],
        "db_snapshot": run_payload["db_snapshot"],
    }
    if include_diagnostic:
        diag = _run_workable_diagnostic(org)
        out["diagnostic"] = diag
    return out


@router.post("/sync/cancel")
def cancel_workable_sync(
    body: _SyncCancelBody | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel an in-progress sync run by run_id, or latest running run when omitted."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)

    run = None
    run_id = body.run_id if body is not None else None
    if run_id is not None:
        run = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.id == run_id,
                WorkableSyncRun.organization_id == org.id,
            )
            .first()
        )
    if run is None:
        run = _latest_running_run_for_org(db, org.id)
    if run is None:
        return {"status": "ok", "message": "No sync in progress.", "run_id": None}

    now = datetime.now(timezone.utc)
    run.cancel_requested_at = now
    org.workable_sync_cancel_requested_at = now
    if run.status == "running":
        run.status = "running"
    db.commit()
    return {
        "status": "ok",
        "message": "Cancel requested. Sync will stop at the next safe checkpoint.",
        "run_id": run.id,
    }


@router.post("/sync")
def run_workable_sync(
    body: _SyncRequestBody | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)

    existing = _latest_running_run_for_org(db, org.id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A sync is already in progress (run_id={existing.id}). Check status below or try again in a few minutes.",
        )

    requested_mode = ((body.mode if body is not None else "metadata") or "metadata").strip().lower()
    if requested_mode not in {"metadata", "full"}:
        requested_mode = "metadata"
    if requested_mode == "full":
        # Reserved in this cycle; execute metadata sync and keep response explicit.
        requested_mode = "metadata"
    selected_job_shortcodes = _normalize_selected_job_shortcodes(body.job_shortcodes if body is not None else None)

    run = WorkableSyncRun(
        organization_id=org.id,
        requested_by_user_id=current_user.id,
        mode=requested_mode,
        status="running",
        phase="queued",
        jobs_total=0,
        jobs_processed=0,
        candidates_seen=0,
        candidates_upserted=0,
        applications_upserted=0,
        errors=[],
        db_snapshot=_db_snapshot_for_org(db, org.id),
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    org.workable_sync_started_at = run.started_at
    org.workable_sync_cancel_requested_at = None
    org.workable_sync_progress = {
        "run_id": run.id,
        "mode": requested_mode,
        "phase": "queued",
        "jobs_total": 0,
        "jobs_processed": 0,
        "candidates_seen": 0,
        "candidates_upserted": 0,
        "applications_upserted": 0,
        "selected_job_shortcodes": selected_job_shortcodes,
        "errors": [],
    }
    db.commit()

    execution_backend = _enqueue_sync(org.id, run.id, requested_mode, selected_job_shortcodes)
    return {
        "status": "started",
        "run_id": run.id,
        "mode": requested_mode,
        "selected_jobs_count": len(selected_job_shortcodes),
        "execution_backend": execution_backend,
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
