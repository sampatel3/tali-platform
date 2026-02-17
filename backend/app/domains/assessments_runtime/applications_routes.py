from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...deps import get_current_user
from ...domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ...models.assessment import Assessment
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.role import (
    ApplicationCreate,
    ApplicationCvUploadResponse,
    ApplicationDetailResponse,
    ApplicationResponse,
    ApplicationUpdate,
    AssessmentFromApplicationCreate,
)
from ...components.integrations.workable.service import WorkableRateLimitError, WorkableService
from ...services.document_service import MAX_FILE_SIZE, extract_text, process_document_upload, save_file_locally
from ...services.fit_matching_service import calculate_cv_job_match_sync
from ...services.assessment_repository_service import (
    AssessmentRepositoryError,
    AssessmentRepositoryService,
)
from .role_support import (
    application_to_response,
    get_application,
    get_role,
    role_has_job_spec,
)

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.applications")


def _refresh_rank_score(app: CandidateApplication) -> None:
    app.rank_score = app.workable_score if app.workable_score is not None else app.cv_match_score


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
        model=settings.resolved_claude_scoring_model,
        additional_requirements=(role.additional_requirements or "").strip() or None,
    )
    app.cv_match_score = result.get("cv_job_match_score")
    app.cv_match_details = result.get("match_details", {})
    app.cv_match_scored_at = datetime.now(timezone.utc)
    _refresh_rank_score(app)
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
    sort_by: str = Query(default="created_at", pattern="^(rank_score|workable_score|cv_match_score|created_at)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    min_rank_score: float | None = Query(default=None),
    min_workable_score: float | None = Query(default=None),
    min_cv_match_score: float | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(manual|workable)$"),
    include_cv_text: bool = Query(False, description="Include full CV text for each application (for viewer)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_role(role_id, current_user.organization_id, db)
    query = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate), joinedload(CandidateApplication.role))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if source:
        query = query.filter(CandidateApplication.source == source)
    if min_rank_score is not None:
        query = query.filter(CandidateApplication.rank_score >= min_rank_score)
    if min_workable_score is not None:
        query = query.filter(CandidateApplication.workable_score >= min_workable_score)
    if min_cv_match_score is not None:
        query = query.filter(CandidateApplication.cv_match_score >= min_cv_match_score)

    sort_map = {
        "created_at": CandidateApplication.created_at,
        "rank_score": CandidateApplication.rank_score,
        "workable_score": CandidateApplication.workable_score,
        "cv_match_score": CandidateApplication.cv_match_score,
    }
    sort_column = sort_map.get(sort_by, CandidateApplication.created_at)
    sort_fn = asc if sort_order == "asc" else desc
    apps = query.order_by(sort_fn(sort_column), CandidateApplication.created_at.desc()).all()

    updated = False
    for app in apps:
        try:
            if app.cv_match_score is None and app.cv_text:
                updated = _compute_cv_match_for_application(app, reset_if_unavailable=False) or updated
            old_rank = app.rank_score
            _refresh_rank_score(app)
            if app.rank_score != old_rank:
                updated = True
        except Exception:
            logger.exception("Failed to update scoring fields for application_id=%s", app.id)
    if updated:
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist backfilled cv_match_score values")

    out = []
    for app in apps:
        data = application_to_response(app)
        payload = data.model_dump()
        if include_cv_text:
            cv = (app.cv_text or "").strip()
            if not cv and app.candidate:
                cv = (app.candidate.cv_text or "").strip()
            payload["cv_text"] = cv or None
        else:
            payload["cv_text"] = None
        out.append(ApplicationDetailResponse(**payload))
    return out


@router.get("/applications/{application_id}", response_model=ApplicationDetailResponse)
def get_application_detail(
    application_id: int,
    include_cv_text: bool = Query(False, description="Include full CV extracted text for viewer"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single application; optionally include full cv_text for CV viewer sidebar."""
    app = get_application(application_id, current_user.organization_id, db)
    data = application_to_response(app)
    payload = data.model_dump()
    if include_cv_text:
        cv = (app.cv_text or "").strip()
        if not cv and app.candidate:
            cv = (app.candidate.cv_text or "").strip()
        payload["cv_text"] = cv or None
    else:
        payload["cv_text"] = None
    return ApplicationDetailResponse(**payload)


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
    _refresh_rank_score(app)
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


@router.post("/applications/{application_id}/generate-taali-cv-ai", response_model=ApplicationDetailResponse)
def generate_taali_cv_ai(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate TAALI CV-vs-job fit score for an application.

    - If the application already has CV text, we just (re)compute the match.
    - If the application is linked to Workable and no CV is present, we attempt to download the resume
      from Workable, extract text, then compute the match.
    """
    app = get_application(application_id, current_user.organization_id, db)
    role = app.role
    if not role or not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before generating TAALI score")

    # If the candidate already has a CV stored, reuse it.
    if (not (app.cv_text or "").strip()) and app.candidate and (app.candidate.cv_text or "").strip():
        app.cv_file_url = app.candidate.cv_file_url
        app.cv_filename = app.candidate.cv_filename
        app.cv_text = app.candidate.cv_text
        app.cv_uploaded_at = app.candidate.cv_uploaded_at

    if not (app.cv_text or "").strip():
        org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
        if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
            raise HTTPException(status_code=400, detail="No CV found for this application (and Workable is not connected)")
        candidate_id = str(app.workable_candidate_id or "").strip()
        if not candidate_id:
            raise HTTPException(status_code=400, detail="No CV found for this application (and it is not linked to a Workable candidate)")

        workable = WorkableService(access_token=org.workable_access_token, subdomain=org.workable_subdomain)
        try:
            candidate_payload = workable.get_candidate(candidate_id)
        except WorkableRateLimitError:
            raise HTTPException(status_code=502, detail="Workable rate limited. Please try again shortly.")

        if not candidate_payload:
            raise HTTPException(status_code=502, detail="Failed to fetch Workable candidate profile")

        downloaded = workable.download_candidate_resume(candidate_payload)
        if not downloaded:
            raise HTTPException(status_code=404, detail="No resume found on the Workable candidate profile")

        filename, content = downloaded
        if not content:
            raise HTTPException(status_code=404, detail="Downloaded resume was empty")
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Resume must be {MAX_FILE_SIZE // (1024 * 1024)}MB or smaller",
            )

        ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
        if ext not in {"pdf", "docx", "txt"}:
            raise HTTPException(status_code=400, detail="Only PDF, DOCX, or TXT resumes are supported")

        now = datetime.now(timezone.utc)
        local_path = save_file_locally(content=content, directory="cv", prefix=f"cv-{application_id}", ext=ext)

        file_url = local_path
        try:
            from ...services.s3_service import generate_s3_key, upload_to_s3

            s3_key = generate_s3_key("cv", application_id, filename)
            s3_url = upload_to_s3(local_path, s3_key)
            if s3_url:
                file_url = s3_url
        except Exception as exc:
            logger.warning("S3 upload skipped for Workable CV import (falling back to local): %s", exc)

        extracted = extract_text(content, ext)
        if not extracted:
            raise HTTPException(status_code=400, detail="Could not extract text from resume. Try uploading the CV manually.")

        app.cv_file_url = file_url
        app.cv_filename = filename
        app.cv_text = extracted
        app.cv_uploaded_at = now
        if app.candidate:
            app.candidate.cv_file_url = file_url
            app.candidate.cv_filename = filename
            app.candidate.cv_text = extracted
            app.candidate.cv_uploaded_at = now

        # Best-effort Workable score extraction from candidate payload.
        raw_score, normalized_score, score_source = workable.extract_workable_score(candidate_payload=candidate_payload)
        if raw_score is not None or normalized_score is not None:
            app.workable_score_raw = raw_score
            app.workable_score = normalized_score
            app.workable_score_source = score_source

    try:
        _compute_cv_match_for_application(app, reset_if_unavailable=True)
    except Exception:
        logger.exception("Failed to compute cv_match_score for application_id=%s", app.id)
        app.cv_match_score = None
        app.cv_match_details = {"error": "Failed to compute CV match score"}
        app.cv_match_scored_at = None
    _refresh_rank_score(app)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate TAALI score")

    app = get_application(app.id, current_user.organization_id, db)
    data = application_to_response(app)
    payload = data.model_dump()
    cv = (app.cv_text or "").strip()
    if not cv and app.candidate:
        cv = (app.candidate.cv_text or "").strip()
    payload["cv_text"] = cv or None
    return ApplicationDetailResponse(**payload)


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
        workable_candidate_id=app.workable_candidate_id,
        workable_job_id=role.workable_job_id,
    )
    db.add(assessment)
    try:
        db.flush()
        repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
        branch_ctx = repo_service.create_assessment_branch(task, assessment.id)
        assessment.assessment_repo_url = branch_ctx.repo_url
        assessment.assessment_branch = branch_ctx.branch_name
        assessment.clone_command = branch_ctx.clone_command

        db.commit()
        db.refresh(assessment)
    except AssessmentRepositoryError:
        db.rollback()
        logger.exception("Assessment repository provisioning failed for assessment_id=%s", assessment.id)
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")
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
    candidate_email = app.candidate.email if app.candidate else None
    if not candidate_email:
        raise HTTPException(status_code=400, detail="Application has no candidate email")
    candidate_name = app.candidate.full_name or app.candidate.email

    if org:
        dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            position=task.name or "Technical assessment",
        )
        try:
            db.commit()
            db.refresh(assessment)
        except Exception:
            db.rollback()
            logger.exception("Failed to persist invite metadata for assessment_id=%s", assessment.id)
    return assessment_to_response(assessment, db)
