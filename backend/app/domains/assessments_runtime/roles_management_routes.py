from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.role import RoleCreate, RoleResponse, RoleTaskLinkRequest, RoleUpdate
from ...services.document_service import process_document_upload
from ...services.interview_focus_service import generate_interview_focus_sync
from .role_support import get_role, role_to_response

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")


def _generate_interview_focus(role: Role) -> tuple[dict | None, str | None]:
    job_spec_text = (role.job_spec_text or "").strip()
    if not job_spec_text:
        return None, "Interview focus unavailable: job spec text is empty"
    if not settings.ANTHROPIC_API_KEY:
        return None, "Interview focus unavailable: Anthropic API key is not configured"

    interview_focus = generate_interview_focus_sync(
        job_spec_text=job_spec_text,
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.resolved_claude_scoring_model,
    )
    if not interview_focus:
        return None, "Interview focus unavailable: failed to generate pointers"
    return interview_focus, None


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = Role(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        description=(data.description or None),
        additional_requirements=(data.additional_requirements or None),
    )
    db.add(role)
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create role")
    return role_to_response(role)


@router.get("/roles")
def list_roles(
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

    return [
        role_to_response(
            role,
            tasks_count=len(role.tasks or []),
            applications_count=app_counts.get(role.id, 0),
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
    if "additional_requirements" in updates:
        role.additional_requirements = updates["additional_requirements"] or None
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")
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

    interview_focus: dict | None = None
    interview_focus_error: str | None = None
    try:
        interview_focus, interview_focus_error = _generate_interview_focus(role)
    except Exception:
        logger.exception("Failed to generate interview focus for role_id=%s", role.id)
        interview_focus = None
        interview_focus_error = "Interview focus unavailable: failed to generate pointers"

    if interview_focus:
        role.interview_focus = interview_focus
        role.interview_focus_generated_at = now

    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload job spec")
    return {
        "success": True,
        "role_id": role.id,
        "filename": result["filename"],
        "text_preview": result["text_preview"],
        "uploaded_at": now,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_error": interview_focus_error,
    }


@router.post("/roles/{role_id}/regenerate-interview-focus")
def regenerate_interview_focus(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regenerate interview focus pointers from the role's job spec. Use after fixing CLAUDE_MODEL."""
    role = get_role(role_id, current_user.organization_id, db)
    now = datetime.now(timezone.utc)
    role.interview_focus = None
    role.interview_focus_generated_at = None

    interview_focus: dict | None = None
    interview_focus_error: str | None = None
    try:
        interview_focus, interview_focus_error = _generate_interview_focus(role)
    except Exception:
        logger.exception("Failed to regenerate interview focus for role_id=%s", role.id)
        interview_focus = None
        interview_focus_error = "Interview focus unavailable: failed to generate pointers"

    if interview_focus:
        role.interview_focus = interview_focus
        role.interview_focus_generated_at = now

    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to regenerate interview focus")

    return {
        "success": True,
        "role_id": role.id,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_error": interview_focus_error,
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
