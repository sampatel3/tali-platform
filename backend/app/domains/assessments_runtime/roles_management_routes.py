from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import RoleCreate, RoleResponse, RoleTaskLinkRequest, RoleUpdate
from ...services.application_events import on_role_jd_attached
from ...services.document_service import process_document_upload
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.role_criteria_service import (
    sync_all_criteria,
    sync_derived_criteria,
    sync_recruiter_criteria,
)
from .role_support import get_role, role_to_response
from .pipeline_service import role_pipeline_counts

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Fall back to the org-wide default scoring criteria when the create
    # request doesn't supply its own. Recruiters can edit the role's
    # additional_requirements afterwards; the default is a starting point,
    # not a binding link.
    requested_reqs = (data.additional_requirements or "").strip() or None
    if requested_reqs is None:
        org = (
            db.query(Organization)
            .filter(Organization.id == current_user.organization_id)
            .first()
        )
        org_default = (
            (getattr(org, "default_additional_requirements", None) or "").strip()
            if org is not None
            else ""
        )
        effective_reqs = org_default or None
    else:
        effective_reqs = requested_reqs

    role = Role(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        description=(data.description or None),
        additional_requirements=effective_reqs,
        screening_pack_template=(data.screening_pack_template.model_dump() if data.screening_pack_template else None),
        tech_interview_pack_template=(data.tech_interview_pack_template.model_dump() if data.tech_interview_pack_template else None),
        auto_reject_enabled=data.auto_reject_enabled,
        auto_reject_threshold_100=data.auto_reject_threshold_100,
        workable_actor_member_id=(data.workable_actor_member_id or None),
        workable_disqualify_reason_id=(data.workable_disqualify_reason_id or None),
        auto_reject_note_template=(data.auto_reject_note_template or None),
    )
    db.add(role)
    try:
        db.flush()
        sync_all_criteria(db, role)
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create role")
    return role_to_response(role)


@router.get("/roles")
def list_roles(
    include_pipeline_stats: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    roles = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc())
        .all()
    )
    if not roles:
        return []

    role_ids = [role.id for role in roles]
    app_counts_rows = (
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.role_id.in_(role_ids),
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    app_counts = {int(role_id): int(total) for role_id, total in app_counts_rows}
    active_counts: dict[int, int] = {}
    last_activity_by_role: dict[int, datetime | None] = {}
    stage_counts_by_role: dict[int, dict[str, int]] = {}

    if include_pipeline_stats:
        active_rows = (
            db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
            .filter(
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.role_id.in_(role_ids),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
        active_counts = {int(role_id): int(total) for role_id, total in active_rows}

        last_activity_rows = (
            db.query(
                CandidateApplication.role_id,
                func.max(
                    func.coalesce(
                        CandidateApplication.pipeline_stage_updated_at,
                        CandidateApplication.updated_at,
                        CandidateApplication.created_at,
                    )
                ),
            )
            .filter(
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(role_ids),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
        last_activity_by_role = {int(role_id): ts for role_id, ts in last_activity_rows}
        for role in roles:
            stage_counts_by_role[role.id] = role_pipeline_counts(
                db,
                organization_id=current_user.organization_id,
                role_id=role.id,
            )

    return [
        role_to_response(
            role,
            tasks_count=len(role.tasks or []),
            applications_count=app_counts.get(role.id, 0),
            stage_counts=stage_counts_by_role.get(role.id, {}),
            active_candidates_count=active_counts.get(role.id, 0),
            last_candidate_activity_at=last_activity_by_role.get(role.id),
        )
        for role in roles
    ]


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role_endpoint(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    app_count = (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    return role_to_response(role, tasks_count=len(role.tasks or []), applications_count=int(app_count))


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    updates = data.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        role.name = updates["name"].strip()
    if "description" in updates:
        role.description = updates["description"] or None
    recruiter_criteria_changed = "additional_requirements" in updates
    if recruiter_criteria_changed:
        role.additional_requirements = updates["additional_requirements"] or None
    if "screening_pack_template" in updates:
        template = updates["screening_pack_template"]
        role.screening_pack_template = template.model_dump() if template else None
    if "tech_interview_pack_template" in updates:
        template = updates["tech_interview_pack_template"]
        role.tech_interview_pack_template = template.model_dump() if template else None
    if "auto_reject_enabled" in updates:
        role.auto_reject_enabled = updates["auto_reject_enabled"]
    if "auto_reject_threshold_100" in updates:
        role.auto_reject_threshold_100 = updates["auto_reject_threshold_100"]
    if "workable_actor_member_id" in updates:
        role.workable_actor_member_id = updates["workable_actor_member_id"] or None
    if "workable_disqualify_reason_id" in updates:
        role.workable_disqualify_reason_id = updates["workable_disqualify_reason_id"] or None
    if "auto_reject_note_template" in updates:
        role.auto_reject_note_template = updates["auto_reject_note_template"] or None
    try:
        if recruiter_criteria_changed:
            sync_recruiter_criteria(db, role)
            mark_role_scores_stale(db, role.id)
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")
    return role_to_response(role)


@router.post("/roles/{role_id}/star", response_model=RoleResponse)
def star_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a role as starred for auto-sync + real-time scoring.

    Side-effect: kick off an immediate Workable sync filtered to this role
    so the recruiter sees fresh candidates within seconds rather than
    waiting up to 15 min for the next Beat tick. Skipped silently for
    manual roles (no workable_job_id) or when another sync is already
    running for the org.
    """
    role = get_role(role_id, current_user.organization_id, db)
    role.starred_for_auto_sync = True
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to star role")

    if (role.source == "workable") and (role.workable_job_id or "").strip():
        try:
            from ..workable_sync.routes import kick_off_filtered_sync

            org = (
                db.query(Organization)
                .filter(Organization.id == current_user.organization_id)
                .first()
            )
            if org is not None:
                kick_off_filtered_sync(
                    db,
                    org=org,
                    job_shortcodes=[str(role.workable_job_id).strip()],
                    requested_by_user_id=current_user.id,
                    mode="full",
                )
        except Exception:
            logger.exception(
                "Failed to kick off immediate sync after starring role_id=%s",
                role.id,
            )

    return role_to_response(role)


@router.delete("/roles/{role_id}/star", response_model=RoleResponse)
def unstar_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    role.starred_for_auto_sync = False
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unstar role")
    return role_to_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    has_applications = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == current_user.organization_id,
        CandidateApplication.role_id == role.id,
    ).first()
    if has_applications:
        raise HTTPException(status_code=400, detail="Cannot delete role with applications")
    in_use = db.query(Assessment).filter(
        Assessment.organization_id == current_user.organization_id,
        Assessment.role_id == role.id,
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Cannot delete role with assessments")
    try:
        db.delete(role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete role")
    return None


@router.post("/roles/{role_id}/upload-job-spec")
def upload_role_job_spec(
    role_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    result = process_document_upload(
        upload=file,
        entity_id=role_id,
        doc_type="job_spec",
        allowed_extensions={"pdf", "docx", "txt"},
    )
    now = datetime.now(timezone.utc)
    role.job_spec_file_url = result["file_url"]
    role.job_spec_filename = result["filename"]
    role.job_spec_text = result["extracted_text"]
    role.description = (result.get("extracted_text") or "").strip() or role.description
    role.job_spec_uploaded_at = now
    role.interview_focus = None
    role.interview_focus_generated_at = None

    try:
        sync_derived_criteria(db, role)
        mark_role_scores_stale(db, role.id)
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload job spec")

    # Auto-trigger interview-focus generation in the background. The
    # request returns immediately; the worker writes interview_focus +
    # pack templates back onto the role row when Claude responds.
    on_role_jd_attached(role)

    return {
        "success": True,
        "role_id": role.id,
        "filename": result["filename"],
        "text_preview": result["text_preview"],
        "uploaded_at": now,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": True,
    }


@router.post("/roles/{role_id}/regenerate-interview-focus")
def regenerate_interview_focus(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regenerate interview focus pointers from the role's job spec. Use after fixing CLAUDE_MODEL."""
    role = get_role(role_id, current_user.organization_id, db)
    role.interview_focus = None
    role.interview_focus_generated_at = None

    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to regenerate interview focus")

    on_role_jd_attached(role)

    return {
        "success": True,
        "role_id": role.id,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": True,
    }


@router.get("/roles/{role_id}/tasks")
def list_role_tasks(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "difficulty": t.difficulty,
            "duration_minutes": t.duration_minutes,
            "task_type": t.task_type,
        }
        for t in (role.tasks or [])
    ]


@router.post("/roles/{role_id}/tasks")
def add_role_task(
    role_id: int,
    data: RoleTaskLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    task = db.query(Task).filter(
        Task.id == data.task_id,
        (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not any(t.id == task.id for t in (role.tasks or [])):
        role.tasks.append(task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to link task to role")
    return {"success": True, "role_id": role.id, "task_id": task.id}


@router.delete("/roles/{role_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role_task(
    role_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    in_use = db.query(Assessment).filter(
        Assessment.organization_id == current_user.organization_id,
        Assessment.role_id == role.id,
        Assessment.task_id == task_id,
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Cannot unlink task that already has assessments")
    role.tasks = [t for t in (role.tasks or []) if t.id != task_id]
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unlink task from role")
    return None
