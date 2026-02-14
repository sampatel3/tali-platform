from __future__ import annotations

import logging
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
)
from ...services.document_service import process_document_upload
from ...services.fit_matching_service import calculate_cv_job_match_sync
from .role_support import (
    application_to_response,
    get_application,
    get_role,
    role_has_job_spec,
)

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.applications")


def _compute_cv_match_for_application(app: CandidateApplication, *, reset_if_unavailable: bool) -> bool:
    """Compute and persist CV-to-job-spec fit score on a role application."""
    role = app.role
    cv_text = (app.cv_text or "").strip()
    job_spec_text = ((role.job_spec_text if role else None) or "").strip()

    if not cv_text or not job_spec_text:
        if reset_if_unavailable:
            app.cv_match_score = None
            app.cv_match_details = None
            app.cv_match_scored_at = None
        return False

    if not settings.ANTHROPIC_API_KEY:
        if reset_if_unavailable:
            app.cv_match_score = None
            app.cv_match_details = {"error": "CV match unavailable: Anthropic API key is not configured"}
            app.cv_match_scored_at = None
        return False

    result = calculate_cv_job_match_sync(
        cv_text=cv_text,
        job_spec_text=job_spec_text,
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.resolved_claude_model,
    )
    app.cv_match_score = result.get("cv_job_match_score")
    app.cv_match_details = result.get("match_details", {})
    app.cv_match_scored_at = datetime.now(timezone.utc)
    return True


@router.post("/roles/{role_id}/applications", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(
    role_id: int,
    data: ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    if not role_has_job_spec(role):
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
    app = get_application(app.id, current_user.organization_id, db)
    return application_to_response(app)


@router.get("/roles/{role_id}/applications")
def list_role_applications(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_role(role_id, current_user.organization_id, db)
    apps = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate), joinedload(CandidateApplication.role))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
        )
        .order_by(CandidateApplication.created_at.desc())
        .all()
    )

    updated = False
    for app in apps:
        if app.cv_match_score is not None:
            continue
        if not app.cv_text:
            continue
        try:
            updated = _compute_cv_match_for_application(app, reset_if_unavailable=False) or updated
        except Exception:
            logger.exception("Failed to backfill cv_match_score for application_id=%s", app.id)
    if updated:
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist backfilled cv_match_score values")
    return [application_to_response(app) for app in apps]


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: int,
    data: ApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
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
    app = get_application(application_id, current_user.organization_id, db)
    return application_to_response(app)


@router.post("/applications/{application_id}/upload-cv", response_model=ApplicationCvUploadResponse)
def upload_application_cv(
    application_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
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
        _compute_cv_match_for_application(app, reset_if_unavailable=True)
    except Exception:
        logger.exception("Failed to compute cv_match_score for application_id=%s", app.id)
        app.cv_match_score = None
        app.cv_match_details = {"error": "Failed to compute CV match score"}
        app.cv_match_scored_at = None
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
    app = get_application(application_id, current_user.organization_id, db)
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
