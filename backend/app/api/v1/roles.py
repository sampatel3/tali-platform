from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...components.notifications.service import send_assessment_invite_sync
from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...schemas.role import (
    ApplicationCreate,
    ApplicationCvUploadResponse,
    ApplicationResponse,
    ApplicationUpdate,
    AssessmentFromApplicationCreate,
    RoleCreate,
    RoleResponse,
    RoleTaskLinkRequest,
    RoleUpdate,
)
from ...services.document_service import process_document_upload

router = APIRouter(tags=["Roles"])


def _role_has_job_spec(role: Role) -> bool:
    return bool((role.job_spec_file_url or "").strip() or (role.job_spec_text or "").strip())


def _get_role(role_id: int, org_id: int, db: Session) -> Role:
    role = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def _get_application(application_id: int, org_id: int, db: Session) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate), joinedload(CandidateApplication.role))
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _role_to_response(role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        description=role.description,
        job_spec_filename=role.job_spec_filename,
        job_spec_uploaded_at=role.job_spec_uploaded_at,
        tasks_count=len(role.tasks or []),
        applications_count=len(role.applications or []),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _application_to_response(app: CandidateApplication) -> ApplicationResponse:
    candidate = app.candidate
    return ApplicationResponse(
        id=app.id,
        organization_id=app.organization_id,
        candidate_id=app.candidate_id,
        role_id=app.role_id,
        status=app.status,
        notes=app.notes,
        candidate_email=(candidate.email if candidate else ""),
        candidate_name=(candidate.full_name if candidate else None),
        candidate_position=(candidate.position if candidate else None),
        cv_filename=app.cv_filename,
        cv_uploaded_at=app.cv_uploaded_at,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


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
    return _role_to_response(role)


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
    return [_role_to_response(role) for role in roles]


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role(
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
    return _role_to_response(role)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _get_role(role_id, current_user.organization_id, db)
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
    return _role_to_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _get_role(role_id, current_user.organization_id, db)
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
    role = _get_role(role_id, current_user.organization_id, db)
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


@router.post("/roles/{role_id}/applications", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(
    role_id: int,
    data: ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _get_role(role_id, current_user.organization_id, db)
    if not _role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before adding applications")
    candidate = db.query(Candidate).filter(
        Candidate.organization_id == current_user.organization_id,
        Candidate.email == str(data.candidate_email),
    ).first()
    if not candidate:
        candidate = Candidate(
            organization_id=current_user.organization_id,
            email=str(data.candidate_email),
            full_name=data.candidate_name or None,
            position=data.candidate_position or None,
        )
        db.add(candidate)
        db.flush()
    else:
        if data.candidate_name:
            candidate.full_name = data.candidate_name
        if data.candidate_position:
            candidate.position = data.candidate_position

    existing = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == current_user.organization_id,
        CandidateApplication.candidate_id == candidate.id,
        CandidateApplication.role_id == role.id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Candidate already has an application for this role")

    app = CandidateApplication(
        organization_id=current_user.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status=data.status or "applied",
        notes=data.notes or None,
    )
    db.add(app)
    try:
        db.commit()
        db.refresh(app)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create application")
    app = _get_application(app.id, current_user.organization_id, db)
    return _application_to_response(app)


@router.get("/roles/{role_id}/applications")
def list_role_applications(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_role(role_id, current_user.organization_id, db)
    apps = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
        )
        .order_by(CandidateApplication.created_at.desc())
        .all()
    )
    return [_application_to_response(app) for app in apps]


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: int,
    data: ApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = _get_application(application_id, current_user.organization_id, db)
    updates = data.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] is not None:
        app.status = updates["status"]
    if "notes" in updates:
        app.notes = updates["notes"] or None
    if app.candidate:
        if "candidate_name" in updates and updates["candidate_name"] is not None:
            app.candidate.full_name = updates["candidate_name"]
        if "candidate_position" in updates and updates["candidate_position"] is not None:
            app.candidate.position = updates["candidate_position"]
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update application")
    app = _get_application(application_id, current_user.organization_id, db)
    return _application_to_response(app)


@router.post("/applications/{application_id}/upload-cv", response_model=ApplicationCvUploadResponse)
def upload_application_cv(
    application_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = _get_application(application_id, current_user.organization_id, db)
    result = process_document_upload(
        upload=file,
        entity_id=application_id,
        doc_type="cv",
        allowed_extensions={"pdf", "docx"},
    )
    now = datetime.now(timezone.utc)
    app.cv_file_url = result["file_url"]
    app.cv_filename = result["filename"]
    app.cv_text = result["extracted_text"]
    app.cv_uploaded_at = now
    if app.candidate:
        app.candidate.cv_file_url = result["file_url"]
        app.candidate.cv_filename = result["filename"]
        app.candidate.cv_text = result["extracted_text"]
        app.candidate.cv_uploaded_at = now
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload CV")
    return ApplicationCvUploadResponse(
        application_id=app.id,
        filename=result["filename"],
        text_preview=result["text_preview"],
        uploaded_at=now,
    )


@router.post("/applications/{application_id}/assessments", status_code=status.HTTP_201_CREATED)
def create_assessment_for_application(
    application_id: int,
    data: AssessmentFromApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = _get_application(application_id, current_user.organization_id, db)
    if not app.cv_file_url:
        raise HTTPException(status_code=400, detail="Upload candidate CV before creating an assessment")
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == app.role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not any(task.id == data.task_id for task in (role.tasks or [])):
        raise HTTPException(status_code=400, detail="Task is not linked to this role")
    task = db.query(Task).filter(
        Task.id == data.task_id,
        (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    token = secrets.token_urlsafe(32)
    assessment = Assessment(
        organization_id=current_user.organization_id,
        candidate_id=app.candidate_id,
        task_id=task.id,
        role_id=role.id,
        application_id=app.id,
        token=token,
        duration_minutes=data.duration_minutes,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
    )
    db.add(assessment)
    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create assessment")

    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(Assessment.id == assessment.id)
        .first()
    )

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "Your recruiter"
    candidate_email = app.candidate.email if app.candidate else None
    if not candidate_email:
        raise HTTPException(status_code=400, detail="Application has no candidate email")
    candidate_name = app.candidate.full_name or app.candidate.email

    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment.id,
            org_name=org_name,
            position=task.name or "Technical assessment",
        )
    else:
        from ...tasks.assessment_tasks import send_assessment_email
        send_assessment_email.delay(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            org_name=org_name,
            position=task.name or "Technical assessment",
            assessment_id=assessment.id,
            request_id=get_request_id(),
        )
    return assessment_to_response(assessment, db)
