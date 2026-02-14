from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import RoleCreate, RoleResponse, RoleTaskLinkRequest, RoleUpdate
from ...services.document_service import process_document_upload
from .role_support import get_role, role_to_response

router = APIRouter(tags=["Roles"])


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
        .options(joinedload(Role.tasks), joinedload(Role.applications))
        .filter(Role.organization_id == current_user.organization_id)
        .order_by(Role.created_at.desc())
        .all()
    )
    return [role_to_response(role) for role in roles]


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role_endpoint(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks), joinedload(Role.applications))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role_to_response(role)


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
    role.job_spec_uploaded_at = now
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
