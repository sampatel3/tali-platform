from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....models.workable_sync_run import WorkableSyncRun
from ....platform.database import SessionLocal
from .service import WorkableService
from .sync_service import WorkableSyncService

logger = logging.getLogger(__name__)


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


def execute_workable_sync_run(
    *,
    org_id: int,
    run_id: int,
    mode: str,
    selected_job_shortcodes: list[str] | None = None,
) -> None:
    """Execute one Workable sync run in a background worker context."""
    db = SessionLocal()
    sync_completed = False
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org or not org.workable_access_token or not org.workable_subdomain:
            logger.error("Workable sync run aborted due to missing org credentials: org_id=%s run_id=%s", org_id, run_id)
            return

        service = WorkableSyncService(
            WorkableService(
                access_token=org.workable_access_token,
                subdomain=org.workable_subdomain,
            )
        )
        service.sync_org(
            db,
            org,
            run_id=run_id,
            mode=mode,
            selected_job_shortcodes=selected_job_shortcodes,
        )
        sync_completed = True
    except Exception as exc:
        logger.exception("Workable background sync failed for org_id=%s run_id=%s: %s", org_id, run_id, exc)
    finally:
        try:
            running = _latest_running_run_for_org(db, org_id)
            if running and running.id == run_id:
                org_row = db.query(Organization).filter(Organization.id == org_id).first()
                if sync_completed:
                    running.status = "success"
                    running.phase = running.phase or "completed"
                    running.finished_at = datetime.now(timezone.utc)
                    if org_row:
                        if not org_row.workable_last_sync_status:
                            org_row.workable_last_sync_at = datetime.now(timezone.utc)
                            org_row.workable_last_sync_status = "success"
                            org_row.workable_last_sync_summary = _run_payload(running, _db_snapshot_for_org(db, org_id))
                        org_row.workable_sync_started_at = None
                        org_row.workable_sync_progress = None
                        org_row.workable_sync_cancel_requested_at = None
                else:
                    running.status = "failed"
                    running.phase = running.phase or "failed"
                    running.finished_at = datetime.now(timezone.utc)
                    errors = list(running.errors or [])
                    if not any("worker failed" in str(e).lower() for e in errors):
                        errors.append("Background worker failed before completion")
                    running.errors = errors
                    if org_row:
                        org_row.workable_last_sync_at = datetime.now(timezone.utc)
                        org_row.workable_last_sync_status = "failed"
                        org_row.workable_last_sync_summary = _run_payload(running, _db_snapshot_for_org(db, org_id))
                        org_row.workable_sync_started_at = None
                        org_row.workable_sync_progress = None
                        org_row.workable_sync_cancel_requested_at = None
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Workable background sync finalization failed for org_id=%s run_id=%s", org_id, run_id)
        finally:
            db.close()
