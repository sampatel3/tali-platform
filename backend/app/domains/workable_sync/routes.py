from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...components.integrations.workable.service import WorkableService as WorkableService
from ...components.integrations.workable import error_policy as workable_error_policy
from ...deps import get_current_user, require_org_owner
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...models.workable_sync_run import WorkableSyncRun
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.admin_auth import require_admin_secret
from ...platform.request_context import get_request_id
from ...services.document_service import (
    sanitize_json_for_storage,
)
from ...services.provider_error_evidence import safe_provider_error_code
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_SOFT_DELETED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version
from ...services.role_lifecycle import stop_role_for_ats_deletion
from .lookup_cache import (
    _LOOKUP_CACHE_FRESH_SECONDS as _LOOKUP_CACHE_FRESH_SECONDS,
    _LOOKUP_CACHE_RETAIN_SECONDS as _LOOKUP_CACHE_RETAIN_SECONDS,
    WorkableRateLimitError as WorkableRateLimitError,
    cached_account_lookup,
    lookup_cache_redis,
)
from .provider_reads import (
    assert_workable_connected as _assert_workable_connected,
    get_org_for_user as _get_org_for_user,
    release_for_workable_provider as _release_for_workable_provider,
    run_workable_diagnostic as _run_workable_diagnostic,
    workable_client_snapshot as _workable_client_snapshot,
)
from .stage_refresh_route import (
    StageRefreshResult,
    refresh_role_workable_stages as refresh_role_workable_stages,
    router as stage_refresh_router,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workable", tags=["Workable"])
router.include_router(stage_refresh_router)
_StageRefreshResult = StageRefreshResult


def _lookup_cache_redis():
    return lookup_cache_redis()


def _cached_account_lookup(subdomain: str | None, kind: str, fetch_fn):
    return cached_account_lookup(
        subdomain,
        kind,
        fetch_fn,
        redis_factory=_lookup_cache_redis,
        log=logger,
        fresh_seconds=_LOOKUP_CACHE_FRESH_SECONDS,
        retain_seconds=_LOOKUP_CACHE_RETAIN_SECONDS,
    )



class _AdminClearSyncBody(BaseModel):
    email: str


class _SyncRequestBody(BaseModel):
    mode: Literal["metadata", "full"] = "full"
    # Legacy compatibility: frontend may still send skip_cv.
    skip_cv: bool | None = None
    # Optional list of Workable job shortcodes/IDs to limit sync scope.
    job_shortcodes: list[str] | None = None


class _SyncCancelBody(BaseModel):
    run_id: int | None = None


@router.get("/admin/diagnostic")
def admin_workable_diagnostic(
    email: str = Query(..., description="User email (e.g. sampatel@deeplight.ae)"),
    _admin: None = Depends(require_admin_secret),
    db: Session = Depends(get_db),
):
    """Run Workable API diagnostic for a user by email."""
    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise HTTPException(status_code=400, detail="email required")
    user = db.query(User).filter(User.email == email_clean).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found: {email_clean}")
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org_id = int(org.id)
    provider_client = _workable_client_snapshot(org)
    _release_for_workable_provider(db)
    diagnostic = _run_workable_diagnostic(provider_client)
    roles = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
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
    _admin: None = Depends(require_admin_secret),
    db: Session = Depends(get_db),
):
    """Clear Workable sync state for a user by email."""
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
    # Also finalize any orphaned ``status='running'`` runs so the next
    # POST /workable/sync isn't blocked by ``_latest_running_run_for_org``.
    # Without this the org flags get cleared but stuck runs (worker died
    # mid-sync, container restart, etc.) still match the "already running"
    # check and the user stays trapped.
    now = datetime.now(timezone.utc)
    stuck_runs = (
        db.query(WorkableSyncRun)
        .filter(
            WorkableSyncRun.organization_id == org.id,
            WorkableSyncRun.finished_at.is_(None),
            WorkableSyncRun.status == "running",
        )
        .all()
    )
    cleared_run_ids: list[int] = []
    for run in stuck_runs:
        run.status = "failed"
        run.finished_at = now
        run.phase = run.phase or "aborted"
        errors = list(run.errors or [])
        errors.append("workable_sync_stale: An orphaned Workable sync was closed safely. Start a new sync.")
        run.errors = errors
        cleared_run_ids.append(run.id)
    db.commit()
    return {
        "status": "ok",
        "message": f"Cleared Workable sync state for {email}. They can start a new sync.",
        "cleared_run_ids": cleared_run_ids,
    }


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


# A worker that dies mid-sync (OOM, SIGKILL, container restart) leaves its run
# row ``status='running'`` with ``finished_at=NULL`` forever, and the in-progress
# guard then locks the org out of all syncs until the 6h ``reap_stuck_workable_sync_runs``
# backstop fires. Keying recovery off the heartbeat (``updated_at`` — bumped as the
# runner writes progress) instead clears a zombie within minutes. A healthy run
# writes progress far more often than this, so it's a safe "is it dead" signal.
_STALE_HEARTBEAT_MINUTES = 30


def _finalize_stale_running_runs(db: Session, org_id: int) -> list[int]:
    """Mark this org's ``running`` runs whose heartbeat has gone stale as failed,
    and clear the org's progress flags, so a fresh sync request recovers from a
    dead worker immediately instead of waiting for the 6h reaper. Returns the
    finalized run ids. Flushes (does not commit) — the caller owns the txn."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_STALE_HEARTBEAT_MINUTES)
    running = (
        db.query(WorkableSyncRun)
        .filter(
            WorkableSyncRun.organization_id == org_id,
            WorkableSyncRun.status == "running",
            WorkableSyncRun.finished_at.is_(None),
        )
        .all()
    )
    cleared: list[int] = []
    for run in running:
        beat = run.updated_at or run.started_at
        if beat is not None and beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        if beat is not None and beat >= cutoff:
            continue  # still heartbeating — leave it alone
        run.status = "failed"
        run.finished_at = now
        run.phase = run.phase or "aborted"
        errors = list(run.errors or [])
        errors.append("workable_sync_stale: A stale Workable sync was closed safely. Start a new sync.")
        run.errors = errors
        cleared.append(int(run.id))
    if cleared:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is not None:
            org.workable_sync_started_at = None
            org.workable_sync_progress = None
            org.workable_sync_cancel_requested_at = None
        db.flush()
    return cleared


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
        "errors": workable_error_policy.public_workable_sync_errors(run.errors),
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
    """Queue the Workable sync run on Celery."""
    from ...tasks.workable_tasks import run_workable_sync_run_task

    run_workable_sync_run_task.delay(
        org_id=org_id,
        run_id=run_id,
        mode=mode,
        selected_job_shortcodes=selected_job_shortcodes or None,
    )
    return "celery"


def kick_off_filtered_sync(
    db: Session,
    *,
    org: Organization,
    job_shortcodes: list[str],
    requested_by_user_id: int | None,
    mode: str = "full",
) -> int | None:
    """Start a Workable sync run filtered to specific job shortcodes.

    Used by the star-role flow to give recruiters near-immediate results
    without waiting for the next 15-min Beat tick. Returns the new run_id,
    or None if the org isn't Workable-connected or another run is already
    in progress (caller doesn't need to do anything in that case — the
    in-flight run will pick up changes).
    """
    if settings.MVP_DISABLE_WORKABLE:
        return None
    if not (org.workable_connected and org.workable_access_token and org.workable_subdomain):
        return None
    if not job_shortcodes:
        return None
    # Recover instantly from a worker that died mid-sync: finalize any
    # stale-heartbeat zombie run so it doesn't block this request until the 6h
    # reaper. A genuinely in-flight run is left alone and still short-circuits.
    cleared = _finalize_stale_running_runs(db, org.id)
    if _latest_running_run_for_org(db, org.id) is not None:
        if cleared:
            db.commit()  # persist the reap even though we won't start a new run
        return None

    requested_mode = (mode or "full").strip().lower()
    if requested_mode not in {"metadata", "full"}:
        requested_mode = "full"

    now = datetime.now(timezone.utc)
    run = WorkableSyncRun(
        organization_id=org.id,
        requested_by_user_id=requested_by_user_id,
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
        started_at=now,
    )
    db.add(run)
    db.flush()
    org.workable_sync_started_at = now
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
        "selected_job_shortcodes": list(job_shortcodes),
        "errors": [],
    }
    db.commit()

    _enqueue_sync(org.id, run.id, requested_mode, list(job_shortcodes))
    return run.id


@router.get("/diagnostic")
def workable_diagnostic(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run Workable API diagnostic for current user's org. For testing integration."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    org_id = int(org.id)
    provider_client = _workable_client_snapshot(org)
    _release_for_workable_provider(db)
    diagnostic = _run_workable_diagnostic(provider_client)

    roles = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
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
    org_id = int(org.id)
    client = _workable_client_snapshot(org)
    if client is None:
        raise HTTPException(status_code=409, detail="Workable connection is incomplete")
    _release_for_workable_provider(db)
    try:
        jobs = client.list_open_jobs()
    except Exception as exc:
        logger.error("Failed listing Workable jobs org_id=%s error_code=%s", org_id, safe_provider_error_code(exc, operation="workable_list_jobs"))
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




@router.get("/members")
def workable_members(
    shortcode: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)
    org_id = int(org.id)
    subdomain = str(org.workable_subdomain or "")
    client = _workable_client_snapshot(org)
    if client is None:
        raise HTTPException(status_code=409, detail="Workable connection is incomplete")
    _release_for_workable_provider(db)
    try:
        # Only the account-level (no-shortcode) list is near-static and worth
        # caching; a job-scoped request bypasses the cache and fetches live.
        if shortcode:
            members = client.list_members(shortcode=shortcode)
        else:
            members = _cached_account_lookup(
                subdomain, "members", client.list_members
            )
    except Exception as exc:
        logger.error("Failed listing Workable members org_id=%s error_code=%s", org_id, safe_provider_error_code(exc, operation="workable_list_members"))
        raise HTTPException(status_code=502, detail="Failed to load Workable members.") from None
    return {"members": members}


@router.get("/disqualification-reasons")
def workable_disqualification_reasons(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)
    org_id = int(org.id)
    subdomain = str(org.workable_subdomain or "")
    client = _workable_client_snapshot(org)
    if client is None:
        raise HTTPException(status_code=409, detail="Workable connection is incomplete")
    _release_for_workable_provider(db)
    try:
        reasons = _cached_account_lookup(
            subdomain,
            "disqualification_reasons",
            client.list_disqualification_reasons,
        )
    except Exception as exc:
        logger.error("Failed listing Workable disqualification reasons org_id=%s error_code=%s", org_id, safe_provider_error_code(exc, operation="workable_list_disqualification_reasons"))
        raise HTTPException(status_code=502, detail="Failed to load Workable disqualification reasons.") from None
    return {"disqualification_reasons": reasons}


@router.get("/stages")
def workable_stages(
    shortcode: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    org_id = int(org.id)

    # Serve a job's stages straight from our DB cache — the periodic sync keeps
    # it fresh, so the picker doesn't pay a live, throttled Workable round-trip
    # on every modal open. This path also works when Workable is briefly
    # unreachable, since it never touches the API.
    role = None
    if shortcode:
        role = (
            db.query(Role)
            .filter(
                Role.organization_id == org_id,
                Role.workable_job_id == shortcode,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if role and role.workable_stages:
            return {"stages": role.workable_stages}

    # Cache miss (role not synced yet, or account-level request with no
    # shortcode): fetch live, and persist the result for next time so the
    # slow path only ever runs once per role.
    _assert_workable_connected(org)
    subdomain = str(org.workable_subdomain or "")
    role_id = int(role.id) if role is not None else None
    client = _workable_client_snapshot(org)
    if client is None:
        raise HTTPException(status_code=409, detail="Workable connection is incomplete")
    _release_for_workable_provider(db)
    try:
        if shortcode:
            stages = client.list_job_stages(shortcode)
        else:
            # Account-level stage list is near-static: cache it and fall back to
            # the last-known-good value on a transient Workable 429.
            stages = _cached_account_lookup(
                subdomain, "stages", client.list_stages
            )
    except Exception as exc:
        logger.error("Failed listing Workable stages org_id=%s error_code=%s", org_id, safe_provider_error_code(exc, operation="workable_list_stages"))
        raise HTTPException(status_code=502, detail="Failed to load Workable stages.") from None
    if shortcode and stages and role_id is not None:
        current_role = (
            db.query(Role)
            .filter(
                Role.id == role_id,
                Role.organization_id == org_id,
                Role.workable_job_id == shortcode,
                Role.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if current_role is not None:
            current_role.workable_stages = sanitize_json_for_storage(stages)
            current_role.workable_stages_synced_at = datetime.now(timezone.utc)
            db.commit()
        else:
            db.rollback()
    return {"stages": stages}


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
        "workable_last_sync_summary": workable_error_policy.public_workable_sync_summary(org.workable_last_sync_summary),
        "workable_sync_progress": workable_error_policy.public_workable_sync_summary(org.workable_sync_progress or run_payload),
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
        provider_client = _workable_client_snapshot(org)
        _release_for_workable_provider(db)
        diag = _run_workable_diagnostic(provider_client)
        out["diagnostic"] = diag
    return out


@router.get("/sync/runs")
def workable_sync_runs(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the most recent Workable sync runs for the caller's org."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    rows = (
        db.query(WorkableSyncRun)
        .filter(WorkableSyncRun.organization_id == org.id)
        .order_by(WorkableSyncRun.id.desc())
        .limit(limit)
        .all()
    )

    def _iso(value):
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    return {
        "runs": [
            {
                "id": r.id,
                "mode": r.mode or "metadata",
                "status": r.status or "running",
                "phase": r.phase,
                "jobs_total": r.jobs_total or 0,
                "jobs_processed": r.jobs_processed or 0,
                "candidates_seen": r.candidates_seen or 0,
                "candidates_upserted": r.candidates_upserted or 0,
                "applications_upserted": r.applications_upserted or 0,
                "errors": workable_error_policy.public_workable_sync_errors(r.errors),
                "started_at": _iso(r.started_at),
                "finished_at": _iso(r.finished_at),
                "cancel_requested_at": _iso(r.cancel_requested_at),
            }
            for r in rows
        ]
    }


@router.post("/sync/cancel")
def cancel_workable_sync(
    body: _SyncCancelBody | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
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
    current_user: User = Depends(require_org_owner),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    org = _get_org_for_user(db, current_user)
    _assert_workable_connected(org)

    existing = _latest_running_run_for_org(db, org.id)
    if existing is not None:
        return JSONResponse(
            status_code=202,
            content={
                "status": "already_running",
                "run_id": existing.id,
                "mode": existing.mode or "full",
                "phase": existing.phase,
                "message": (
                    f"A sync is already in progress (run_id={existing.id}). "
                    "Polling the existing background run instead of starting a new one."
                ),
                "execution_backend": "existing",
            },
        )

    org_config = org.workable_config if isinstance(org.workable_config, dict) else {}
    configured_mode = str(org_config.get("default_sync_mode") or "full").strip().lower()
    requested_mode = ((body.mode if body is not None else configured_mode) or configured_mode).strip().lower()
    if requested_mode not in {"metadata", "full"}:
        requested_mode = configured_mode if configured_mode in {"metadata", "full"} else "full"
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
    current_user: User = Depends(require_org_owner),
):
    """Soft-delete all Workable-imported roles, applications, and candidates for this org.
    Records are marked with deleted_at; they are not physically removed.
    """
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    _get_org_for_user(db, current_user)
    org_id = current_user.organization_id
    now = datetime.now(timezone.utc)

    # Lock every affected role in one deterministic order before mutating any
    # of them. This serializes Clear with per-role controls and Workable upserts
    # without creating an A->B / B->A deadlock pattern.
    locked_rows = (
        db.query(Role.id, Role.version)
        .filter(Role.organization_id == org_id, Role.source == "workable")
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .all()
    )
    roles_updated = 0
    request_id = get_request_id()
    for locked in locked_rows:
        role = db.get(Role, int(locked.id))
        if role is None:
            continue
        if int(role.version or 1) != int(locked.version or 1):
            db.refresh(role)
        was_live = role.deleted_at is None
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        changed = stop_role_for_ats_deletion(
            role,
            deleted_at=now,
            provider="Workable",
        )
        if not changed:
            continue
        audit_to = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_SOFT_DELETED,
            actor_user_id=int(current_user.id),
            from_version=audit_from,
            to_version=audit_to,
            reason="Workable data cleared; agent turned off",
            request_id=request_id,
        )
        if was_live:
            roles_updated += 1
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
