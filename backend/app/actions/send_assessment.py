"""Create an assessment for an application and dispatch the invite email.

This is the agent's auto-execute path for "send the candidate the
assessment". The recruiter UI calls a similar flow at
``recruiter_management_routes.create_assessment``; this action wraps the
same lower-level helpers (creation gate, repo provisioning, invite
dispatch) into a thin function the agent can call without going through
HTTP.

The action is intentionally restrictive for the agent:
- It requires ``application_id`` (no candidate-only path).
- It picks the assessment task automatically when the role has exactly
  one linked task; if the role has 0 or >1 tasks, the action refuses and
  expects the recruiter to handle it.
- It uses a fixed default duration. Recruiter UI lets users pick; the
  agent doesn't need that knob.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..components.assessments.repository import utcnow
from ..components.assessments.service import get_assessment_creation_gate
from ..domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from ..domains.assessments_runtime.role_support import (
    get_application,
    latest_valid_role_assessment,
)
from ..models.assessment import Assessment
from ..models.role import Role
from ..platform.config import settings
from ..services.assessment_repository_service import (
    AssessmentRepositoryError,
    AssessmentRepositoryService,
)
from ..services.experiment_assignment import (
    RoleTaskMisconfigured,
    resolve_task_and_variant,
)
from .types import ACTOR_AGENT, Actor


logger = logging.getLogger("taali.actions.send_assessment")


_DEFAULT_DURATION_MINUTES = 90


class SendAssessmentResult:
    """Lightweight result wrapper so callers can inspect status without re-querying."""

    def __init__(self, assessment: Optional[Assessment], status: str, detail: Optional[str] = None):
        self.assessment = assessment
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": int(self.assessment.id) if self.assessment is not None else None,
            "status": self.status,
            "detail": self.detail,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    task_id: Optional[int] = None,
    duration_minutes: int = _DEFAULT_DURATION_MINUTES,
) -> SendAssessmentResult:
    """Send an assessment invite for ``application_id``.

    Returns ``SendAssessmentResult`` with ``status`` one of:
    - ``"sent"``: assessment created and invite dispatched
    - ``"already_exists"``: a valid assessment already exists for this
      candidate+role pair (idempotent — agent should not retry)
    - ``"insufficient_credits"``: the org's billing gate refused
    - ``"misconfigured"``: role has 0 or ambiguous task linkage; recruiter
      action required
    """
    if duration_minutes < 15 or duration_minutes > 180:
        raise HTTPException(
            status_code=422, detail="duration_minutes must be between 15 and 180"
        )

    app = get_application(application_id, organization_id, db)
    if app.role_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"application {application_id} has no role — cannot send assessment",
        )

    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == app.role_id, Role.organization_id == organization_id)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {app.role_id} not found")

    candidate = app.candidate
    if candidate is None or not (candidate.email or "").strip():
        raise HTTPException(
            status_code=422,
            detail=f"application {application_id} has no candidate email",
        )

    try:
        choice = resolve_task_and_variant(
            db,
            role,
            candidate_id=int(candidate.id),
            organization_id=organization_id,
            task_id=task_id,
        )
    except RoleTaskMisconfigured as exc:
        return SendAssessmentResult(None, "misconfigured", exc.detail)
    task = choice.task

    # Apply the design-knob "duration" override at invite time (frozen on the
    # assessment). Knob is clamped to the same 15–180 bound as the arg.
    knobs = choice.knob_overrides or {}
    effective_duration = int(duration_minutes)
    if knobs.get("duration_minutes") is not None:
        effective_duration = max(15, min(180, int(knobs["duration_minutes"])))

    # Idempotency: refuse if a valid assessment already exists.
    existing = latest_valid_role_assessment(
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        org_id=int(organization_id),
        db=db,
    )
    if existing is not None:
        return SendAssessmentResult(existing, "already_exists")

    # Billing gate.
    gate = get_assessment_creation_gate(
        organization_id, db, lock_organization=True
    )
    if not gate.get("can_create"):
        return SendAssessmentResult(
            None,
            "insufficient_credits" if gate.get("reason") == "insufficient_credits" else "blocked",
            str(gate.get("message") or gate.get("reason") or ""),
        )
    org = gate.get("organization")
    org_feedback_enabled = bool(getattr(org, "candidate_feedback_enabled", True)) if org else True

    # Pipeline: invited.
    ensure_pipeline_fields(app)
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="system",
        actor_id=actor.event_actor_id,
        reason="Pipeline initialized before assessment send",
    )
    transition_stage(
        db,
        app=app,
        to_stage="invited",
        source=actor.type,
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=("Assessment invite sent by agent" if actor.type == ACTOR_AGENT else "Assessment invite sent"),
        metadata={
            "assessment_mode": "agent_send" if actor.type == ACTOR_AGENT else "manual",
            "task_id": int(task.id),
            "assignment_method": choice.method,
            "experiment_id": int(choice.experiment.id) if choice.experiment is not None else None,
        },
    )

    # Create Assessment row.
    token = secrets.token_urlsafe(32)
    assessment = Assessment(
        organization_id=organization_id,
        candidate_id=int(candidate.id),
        task_id=int(task.id),
        role_id=int(role.id),
        application_id=int(app.id),
        token=token,
        duration_minutes=effective_duration,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        workable_candidate_id=app.workable_candidate_id,
        workable_job_id=role.workable_job_id,
        candidate_feedback_enabled=org_feedback_enabled,
        experiment_id=int(choice.experiment.id) if choice.experiment is not None else None,
        experiment_arm_id=int(choice.arm.id) if choice.arm is not None else None,
        assignment_method=choice.method,
        assignment_key=choice.assignment_key,
        knob_variant_applied=choice.knob_overrides,
        score_weights_override=knobs.get("score_weights"),
        calibration_enabled=knobs.get("calibration_enabled"),
    )
    db.add(assessment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Race: another path created the assessment between our gate check
        # and flush. Return the existing one.
        existing = latest_valid_role_assessment(
            candidate_id=int(candidate.id),
            role_id=int(role.id),
            org_id=int(organization_id),
            db=db,
        )
        if existing is not None:
            return SendAssessmentResult(existing, "already_exists")
        logger.exception("send_assessment: integrity error with no existing assessment found")
        raise HTTPException(status_code=500, detail="Failed to create assessment") from exc

    # Provision GitHub branch (same as recruiter flow).
    try:
        repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
        branch_ctx = repo_service.create_assessment_branch(task, int(assessment.id))
        assessment.assessment_repo_url = branch_ctx.repo_url
        assessment.assessment_branch = branch_ctx.branch_name
        assessment.clone_command = branch_ctx.clone_command
    except AssessmentRepositoryError:
        db.rollback()
        logger.exception(
            "send_assessment: repo provisioning failed assessment_id=%s", assessment.id
        )
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")

    db.flush()

    # Dispatch invite (Workable-first hybrid with manual fallback).
    if org is not None:
        from ..domains.integrations_notifications.invite_flow import dispatch_assessment_invite

        try:
            dispatch_assessment_invite(
                assessment=assessment,
                org=org,
                candidate_email=candidate.email,
                candidate_name=candidate.full_name or candidate.email,
                position=task.name or "Technical assessment",
            )
        except Exception:  # pragma: no cover — invite dispatch is best-effort
            logger.exception(
                "send_assessment: invite dispatch failed assessment_id=%s", assessment.id
            )

    return SendAssessmentResult(assessment, "sent")
