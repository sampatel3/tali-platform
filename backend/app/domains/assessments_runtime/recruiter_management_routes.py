from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...components.assessments.service import get_assessment_creation_gate
from ...deps import get_current_user
from ...domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ...models.assessment import Assessment
from ...models.assessment_experiment import ASSIGNMENT_METHOD_FORCED
from ...services.experiment_assignment import (
    RoleTaskMisconfigured,
    resolve_task_and_variant,
)
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
from .pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from .role_support import latest_valid_role_assessment

router = APIRouter()
logger = logging.getLogger("taali.assessments")


def _assessment_create_conflict(existing: Assessment) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "A valid assessment already exists for this candidate and role. Use retake instead.",
            "code": "retake_required",
            "assessment_id": existing.id,
            "assessment_status": (
                existing.status.value if hasattr(existing.status, "value") else str(existing.status)
            ),
        },
    )


def _is_active_role_assessment_integrity_error(err: Exception) -> bool:
    if not isinstance(err, IntegrityError):
        return False
    message = str(getattr(err, "orig", err)).lower()
    return (
        "uq_assessments_candidate_role_active" in message
        or ("assessments.candidate_id" in message and "assessments.role_id" in message and "unique" in message)
    )


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
        # task_id is optional: when omitted we let an active A/B experiment on
        # the role assign the arm (resolved once the role is known, below).
        task = db.query(Task).filter(
            Task.id == data.task_id,
            (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
        ).first() if data.task_id is not None else None
        if data.task_id is not None and not task:
            raise HTTPException(status_code=404, detail="Task not found")
        arm_choice = None
        creation_gate = get_assessment_creation_gate(
            current_user.organization_id,
            db,
            lock_organization=True,
        )
        if not creation_gate.get("can_create"):
            raise HTTPException(status_code=402, detail=creation_gate.get("message"))
        org_record = creation_gate.get("organization")
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
            if task is not None and not any(t.id == task.id for t in (role.tasks or [])):
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
                if task is not None and not any(t.id == task.id for t in (role.tasks or [])):
                    raise HTTPException(status_code=400, detail="Task is not linked to the selected role")
                resolved_role = role
            candidate_email = candidate.email
            candidate_name = candidate.full_name or candidate.email

        # No explicit task picked → route through the shared experiment
        # chokepoint so an active A/B on the role assigns the arm
        # (deterministic + stable per candidate). Mirrors the agent send path.
        if task is None:
            if resolved_role is None:
                raise HTTPException(
                    status_code=400,
                    detail="task_id is required when the assessment is not tied to a role",
                )
            try:
                arm_choice = resolve_task_and_variant(
                    db,
                    resolved_role,
                    candidate_id=int(candidate.id),
                    organization_id=current_user.organization_id,
                    task_id=None,
                )
            except RoleTaskMisconfigured as exc:
                raise HTTPException(status_code=422, detail=str(exc.detail))
            task = arm_choice.task

        if resolved_role_id:
            existing = latest_valid_role_assessment(
                candidate_id=(candidate.id if candidate else None),
                role_id=resolved_role_id,
                org_id=current_user.organization_id,
                db=db,
            )
            if existing is not None:
                raise _assessment_create_conflict(existing)

        if application is not None:
            ensure_pipeline_fields(application)
            initialize_pipeline_event_if_missing(
                db,
                app=application,
                actor_type="system",
                actor_id=current_user.id,
                reason="Pipeline initialized before recruiter assessment create",
            )
            transition_stage(
                db,
                app=application,
                to_stage="invited",
                source="recruiter",
                actor_type="recruiter",
                actor_id=current_user.id,
                reason="Assessment invite created",
                metadata={"assessment_mode": "recruiter_management"},
            )

        token = secrets.token_urlsafe(32)
        assessment = Assessment(
            organization_id=current_user.organization_id,
            candidate_id=candidate.id,
            task_id=task.id,
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
            # task_id given → FORCED (excluded from the experiment's analysis
            # cohort). task_id omitted → the shared resolver assigned the arm
            # (random/stable) or a single-task default; carry its assignment
            # metadata so the recruiter path and the agent path stay consistent.
            assignment_method=(arm_choice.method if arm_choice else ASSIGNMENT_METHOD_FORCED),
            experiment_id=(int(arm_choice.experiment.id) if arm_choice and arm_choice.experiment else None),
            experiment_arm_id=(int(arm_choice.arm.id) if arm_choice and arm_choice.arm else None),
            assignment_key=(arm_choice.assignment_key if arm_choice else None),
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
    except Exception as exc:
        db.rollback()
        if resolved_role_id and _is_active_role_assessment_integrity_error(exc):
            existing = latest_valid_role_assessment(
                candidate_id=(candidate.id if candidate else None),
                role_id=resolved_role_id,
                org_id=current_user.organization_id,
                db=db,
            )
            if existing is not None:
                raise _assessment_create_conflict(existing)
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
            # Route candidate replies to the recruiter who triggered the
            # send rather than the platform's no-reply address.
            reply_to=current_user.email,
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
    include_voided: bool = Query(default=False),
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
    if not include_voided:
        q = q.filter(Assessment.is_voided.is_(False))
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
    from ...actions import Actor, resend_assessment_invite as action

    result = action.run(
        db,
        Actor.recruiter(current_user),
        organization_id=int(current_user.organization_id),
        assessment_id=assessment_id,
    )
    if result.status == "voided":
        raise HTTPException(status_code=400, detail=result.detail)
    if result.status == "no_candidate":
        raise HTTPException(status_code=400, detail=result.detail)
    db.commit()
    return {"success": True}
