"""Durable API surface for the unified candidate Process cascade."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...platform.database import get_db
from ...services.process_role_dispatch import (
    claim_process_publish,
    ensure_process_role_intent,
    latest_process_role_run,
    mark_process_dispatched,
    progress_from_run,
    request_process_cancel,
)
from .applications_routes import (
    _PIPELINE_STAGE_VALUES,
    _PROCESS_CANCEL_PREFIX,
    _clear_cancel_flag,
    _empty_process_progress,
    _process_dry_run,
    _process_progress,
    _read_process_progress,
    _set_cancel_flag,
    _set_process_progress,
)
from .job_authorization import JobPermission, require_job_permission
from .role_support import role_has_job_spec


router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.process_routes")


def _validated_options(
    *,
    role_id: int,
    organization_id: int,
    payload: dict,
    db: Session,
) -> dict:
    fetch_cvs = bool(payload.get("fetch_cvs"))
    refresh_cvs = bool(payload.get("refresh_cvs"))
    if refresh_cvs:
        fetch_cvs = True
    pre_screen = bool(payload.get("pre_screen"))
    refresh_pre_screen = bool(payload.get("refresh_pre_screen"))
    score_mode = str(payload.get("score") or "none").lower()
    sync_graph = bool(payload.get("sync_graph"))
    refresh_graph = bool(payload.get("refresh_graph"))
    stage_raw = str(payload.get("stage") or "").strip().lower() or None
    if stage_raw and stage_raw not in ("all", "rejected", *_PIPELINE_STAGE_VALUES):
        raise HTTPException(
            status_code=400,
            detail=(
                "stage must be one of: all, applied, invited, "
                "in_assessment, review, advanced, rejected"
            ),
        )
    stage_filter = None if stage_raw in (None, "all") else stage_raw

    raw_ids = payload.get("application_ids")
    application_ids: list[int] | None = None
    if raw_ids:
        if not isinstance(raw_ids, list):
            raise HTTPException(
                status_code=400,
                detail="application_ids must be a list of integers",
            )
        try:
            requested_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="application_ids must be integers",
            ) from exc
        owned = {
            int(row.id)
            for row in db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.id.in_(requested_ids),
                CandidateApplication.role_id == int(role_id),
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        }
        application_ids = [value for value in requested_ids if value in owned]
        if requested_ids and not application_ids:
            raise HTTPException(
                status_code=400,
                detail="None of the requested application_ids belong to this role",
            )

    if score_mode not in ("none", "new", "all"):
        raise HTTPException(
            status_code=400,
            detail="score must be one of: none, new, all",
        )
    if not (
        fetch_cvs
        or pre_screen
        or refresh_pre_screen
        or score_mode != "none"
        or sync_graph
    ):
        raise HTTPException(status_code=400, detail="Pick at least one step to run")
    return {
        "fetch_cvs": fetch_cvs,
        "refresh_cvs": refresh_cvs,
        "pre_screen": pre_screen,
        "refresh_pre_screen": refresh_pre_screen,
        "score_mode": score_mode,
        "sync_graph": sync_graph,
        "refresh_graph": refresh_graph,
        "stage_filter": stage_filter,
        "application_ids": application_ids,
    }


@router.post("/roles/{role_id}/process")
def process_role(
    role_id: int,
    payload: dict = Body(default={}),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview or start a durable fetch → pre-screen → score → graph run."""

    organization_id = int(current_user.organization_id)
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    options = _validated_options(
        role_id=role_id,
        organization_id=organization_id,
        payload=payload,
        db=db,
    )
    if (
        options["pre_screen"]
        or options["refresh_pre_screen"]
        or options["score_mode"] != "none"
    ) and not role_has_job_spec(role):
        raise HTTPException(
            status_code=400,
            detail="Upload job spec before pre-screen or scoring",
        )

    if dry_run:
        counts = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=organization_id,
            **options,
        )
        counts.update(
            {
                "role_name": role.name,
                "stage": options["stage_filter"],
                "selected_count": len(options["application_ids"] or []),
            }
        )
        return counts

    progress = _empty_process_progress()
    progress.update(
        {
            "status": "queued",
            "role_name": role.name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "score": {**progress["score"], "mode": options["score_mode"]},
        }
    )
    recovery_payload = {
        **options,
        "user_id": int(current_user.id) if current_user.id is not None else None,
    }
    intent = ensure_process_role_intent(
        db,
        role_id=role_id,
        organization_id=organization_id,
        payload=recovery_payload,
        progress=progress,
    )
    if not intent.created:
        existing = progress_from_run(intent.run)
        db.commit()
        _set_process_progress(role_id, existing)
        return {
            **existing,
            "run_status": existing.get("status"),
            "status": "already_running",
        }

    publish_payload = claim_process_publish(intent.run)
    if publish_payload is None:
        raise HTTPException(status_code=503, detail="Process dispatch unavailable")
    # The intent and its retry reservation must commit before broker I/O.
    db.commit()
    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
    progress = progress_from_run(intent.run)
    _set_process_progress(role_id, progress)

    from ...tasks.prescreen_tasks import process_role_job

    dispatch_pending = False
    try:
        process_role_job.delay(**publish_payload)
    except Exception:
        # Beat owns the persisted retry.  Do not mark the run failed or make
        # the recruiter repeat an action that may already have been accepted.
        dispatch_pending = True
        logger.exception("Process broker publish failed run_id=%s", intent.run.id)
    else:
        mark_process_dispatched(db, run_id=int(intent.run.id))
        db.commit()

    return {
        **progress,
        "stage": options["stage_filter"],
        "selected_count": len(options["application_ids"] or []),
        "dispatch_pending": dispatch_pending,
    }


@router.get("/roles/{role_id}/process/status")
def process_role_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return durable Process progress, with Redis/local legacy fallback."""

    organization_id = int(current_user.organization_id)
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.VIEW,
        lock_for_update=False,
    )
    run = latest_process_role_run(
        db,
        role_id=role_id,
        organization_id=organization_id,
    )
    progress = (
        progress_from_run(run)
        if run is not None
        else _read_process_progress(role_id)
        or _process_progress.get(role_id)
        or _empty_process_progress()
    )
    result = dict(progress)
    if not result.get("role_name"):
        result["role_name"] = role.name
    return result


@router.post("/roles/{role_id}/process/cancel")
def process_role_cancel(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = int(current_user.organization_id)
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    run = request_process_cancel(
        db,
        role_id=role_id,
        organization_id=organization_id,
    )
    db.commit()
    _set_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
    progress = progress_from_run(run) if run is not None else _empty_process_progress()
    _set_process_progress(role_id, progress)
    return {"ok": True, "role_id": role_id, "status": progress.get("status", "idle")}


__all__ = ["router"]
