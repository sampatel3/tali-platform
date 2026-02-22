from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...deps import get_current_user
from ...domains.integrations_notifications.adapters import build_workable_adapter
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
from ...schemas.assessment import AssessmentCreate, AssessmentResponse
from ...services.assessment_repository_service import (
    AssessmentRepositoryError,
    AssessmentRepositoryService,
)

router = APIRouter()
logger = logging.getLogger("taali.assessments")


@router.post("/", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
def create_assessment(
    data: AssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new assessment and send invite email to candidate."""
    if data.duration_minutes < 15 or data.duration_minutes > 180:
        raise HTTPException(status_code=400, detail="duration_minutes must be between 15 and 180")

    candidate = None
    application = None
    resolved_role = None
    resolved_role_id = data.role_id
    candidate_email = None
    candidate_name = None
    org_feedback_enabled = True

    try:
        task = db.query(Task).filter(
            Task.id == data.task_id,
            (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        org_record = (
            db.query(Organization)
            .filter(Organization.id == current_user.organization_id)
            .first()
        )
        if org_record is not None:
            org_feedback_enabled = bool(getattr(org_record, "candidate_feedback_enabled", True))

        if data.application_id:
            application = (
                db.query(CandidateApplication)
                .options(joinedload(CandidateApplication.candidate), joinedload(CandidateApplication.role))
                .filter(
                    CandidateApplication.id == data.application_id,
                    CandidateApplication.organization_id == current_user.organization_id,
                )
                .first()
            )
            if not application:
                raise HTTPException(status_code=404, detail="Application not found")
            candidate = application.candidate
            if not candidate:
                raise HTTPException(status_code=400, detail="Application has no candidate")
            resolved_role_id = application.role_id
            if data.role_id and data.role_id != resolved_role_id:
                raise HTTPException(status_code=400, detail="application_id and role_id do not match")

            role = (
                db.query(Role)
                .options(joinedload(Role.tasks))
                .filter(Role.id == resolved_role_id, Role.organization_id == current_user.organization_id)
                .first()
            )
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")
            if not any(t.id == task.id for t in (role.tasks or [])):
                raise HTTPException(status_code=400, detail="Task is not linked to the selected role")
            resolved_role = role
            candidate_email = candidate.email
            candidate_name = candidate.full_name or candidate.email
        else:
            if not data.candidate_email:
                raise HTTPException(status_code=400, detail="application_id or candidate_email is required")
            candidate = db.query(Candidate).filter(
                Candidate.email == data.candidate_email,
                Candidate.organization_id == current_user.organization_id,
            ).first()
            if not candidate:
                candidate = Candidate(
                    email=data.candidate_email,
                    full_name=data.candidate_name or None,
                    organization_id=current_user.organization_id,
                )
                db.add(candidate)
                db.flush()
            elif data.candidate_name:
                candidate.full_name = data.candidate_name

            if resolved_role_id:
                role = (
                    db.query(Role)
                    .options(joinedload(Role.tasks))
                    .filter(Role.id == resolved_role_id, Role.organization_id == current_user.organization_id)
                    .first()
                )
                if not role:
                    raise HTTPException(status_code=404, detail="Role not found")
                if not any(t.id == task.id for t in (role.tasks or [])):
                    raise HTTPException(status_code=400, detail="Task is not linked to the selected role")
                resolved_role = role
            candidate_email = candidate.email
            candidate_name = candidate.full_name or candidate.email

        token = secrets.token_urlsafe(32)
        assessment = Assessment(
            organization_id=current_user.organization_id,
            candidate_id=candidate.id,
            task_id=data.task_id,
            role_id=resolved_role_id,
            application_id=(application.id if application else None),
            token=token,
            duration_minutes=data.duration_minutes,
            expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
            workable_candidate_id=(
                application.workable_candidate_id if application else getattr(candidate, "workable_candidate_id", None)
            ),
            workable_job_id=(resolved_role.workable_job_id if resolved_role else None),
            candidate_feedback_enabled=org_feedback_enabled,
        )
        db.add(assessment)
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
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to create assessment")
        raise HTTPException(status_code=500, detail="Failed to create assessment")

    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.application),
        )
        .filter(Assessment.id == assessment.id)
        .first()
    )

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
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
    return assessment_to_response(assessment, db)


@router.get("/")
def list_assessments(
    status: Optional[str] = None,
    task_id: Optional[int] = None,
    candidate_id: Optional[int] = None,
    role_id: Optional[int] = None,
    application_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List assessments for the current user's organization."""
    q = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.application),
        )
        .filter(Assessment.organization_id == current_user.organization_id)
    )
    if status:
        q = q.filter(Assessment.status == status)
    if task_id is not None:
        q = q.filter(Assessment.task_id == task_id)
    if candidate_id is not None:
        q = q.filter(Assessment.candidate_id == candidate_id)
    if role_id is not None:
        q = q.filter(Assessment.role_id == role_id)
    if application_id is not None:
        q = q.filter(Assessment.application_id == application_id)
    q = q.order_by(Assessment.created_at.desc())
    total = q.count()
    assessments = q.offset(offset).limit(limit).all()
    return {
        "items": [assessment_to_response(a, db) for a in assessments],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single assessment by ID."""
    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.application),
        )
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment_to_response(assessment, db)


@router.delete("/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    try:
        db.delete(assessment)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete assessment")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{assessment_id}/resend")
def resend_assessment_invite(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not assessment.candidate:
        raise HTTPException(status_code=400, detail="Assessment has no candidate")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if org:
        dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name or assessment.candidate.email,
            position=(assessment.task.name if assessment.task else "Technical assessment"),
        )
        db.commit()
    return {"success": True}


@router.post("/{assessment_id}/post-to-workable")
def post_assessment_to_workable(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.posted_to_workable:
        return {
            "success": True,
            "already_posted": True,
            "posted_to_workable": True,
            "posted_to_workable_at": assessment.posted_to_workable_at,
        }

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        raise HTTPException(status_code=400, detail="Workable is not connected")
    if not assessment.workable_candidate_id:
        raise HTTPException(status_code=400, detail="Assessment is not linked to a Workable candidate")

    svc = build_workable_adapter(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = svc.post_assessment_result(
        candidate_id=assessment.workable_candidate_id,
        assessment_data={
            "score": assessment.score or 0,
            "tests_passed": assessment.tests_passed or 0,
            "tests_total": assessment.tests_total or 0,
            "time_taken": assessment.duration_minutes,
            "results_url": f"{settings.FRONTEND_URL}/dashboard",
        },
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail="Failed to post to Workable")

    assessment.posted_to_workable = True
    assessment.posted_to_workable_at = utcnow()
    db.commit()
    return {
        "success": True,
        "posted_to_workable": True,
        "posted_to_workable_at": assessment.posted_to_workable_at,
    }
