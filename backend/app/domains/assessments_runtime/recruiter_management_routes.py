from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...components.notifications.service import send_assessment_invite_sync
from ...deps import get_current_user
from ...domains.integrations_notifications.adapters import build_workable_adapter
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
from ...schemas.assessment import AssessmentCreate, AssessmentResponse

router = APIRouter()


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
    resolved_role_id = data.role_id
    candidate_email = None
    candidate_name = None

    try:
        task = db.query(Task).filter(
            Task.id == data.task_id,
            (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

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
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("Failed to create assessment")
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
    org_name = org.name if org else "Your recruiter"
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
    org_name = org.name if org else "Your recruiter"
    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name or assessment.candidate.email,
            token=assessment.token,
            assessment_id=assessment.id,
            org_name=org_name,
            position=(assessment.task.name if assessment.task else "Technical assessment"),
        )
    else:
        from ...tasks.assessment_tasks import send_assessment_email

        send_assessment_email.delay(
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name or assessment.candidate.email,
            token=assessment.token,
            org_name=org_name,
            position=(assessment.task.name if assessment.task else "Technical assessment"),
            assessment_id=assessment.id,
            request_id=get_request_id(),
        )
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
