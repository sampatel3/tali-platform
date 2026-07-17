"""Resend an existing assessment invite (no new Assessment row).

Used when a candidate didn't receive or lost the original invite. Both
the recruiter UI and the agent invoke this through the same action so
the audit trail and pipeline event are identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..components.assessments.repository import utcnow
from ..domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ..models.assessment import Assessment, AssessmentStatus
from ..models.organization import Organization
from ..platform.config import settings
from .types import ACTOR_AGENT, ACTOR_SYSTEM, Actor


logger = logging.getLogger("taali.actions.resend_assessment_invite")


@dataclass(frozen=True)
class ResendAssessmentInviteResult:
    assessment_id: int
    status: str  # "queued" | "voided" | "no_candidate" | "blocked"
    detail: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id,
            "status": self.status,
            "detail": self.detail,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    assessment_id: int,
) -> ResendAssessmentInviteResult:
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == organization_id,
        )
        .first()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if bool(getattr(assessment, "is_voided", False)):
        return ResendAssessmentInviteResult(
            assessment_id=int(assessment.id),
            status="voided",
            detail="Voided assessments cannot be resent",
        )
    if actor.type in {ACTOR_AGENT, ACTOR_SYSTEM}:
        from ..services.role_execution_guard import (
            assessment_task_is_current,
            automatic_role_action_block_reason,
            lock_live_role,
        )

        if assessment.role_id is None:
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail="Automatic invite resend held: assessment has no role",
            )
        role = lock_live_role(
            db,
            role_id=int(assessment.role_id),
            organization_id=int(organization_id),
        )
        block_reason = automatic_role_action_block_reason(role, db=db)
        if block_reason:
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=f"Automatic invite resend held: {block_reason}",
            )
        if role is None or not assessment_task_is_current(
            db, assessment=assessment, role=role
        ):
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=(
                    "Automatic invite resend held: the assessment task was "
                    "superseded or is no longer assignable for this role"
                ),
            )
    candidate = assessment.candidate
    if candidate is None or not (candidate.email or "").strip():
        return ResendAssessmentInviteResult(
            assessment_id=int(assessment.id),
            status="no_candidate",
            detail="Assessment has no candidate email",
        )

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Automatic recovery must retain one stable provider idempotency key, but
    # an explicit resend is a new logical candidate email.  Advance the durable
    # generation before registering the outbox intent so every retry of this
    # resend shares a new key without being collapsed into the original send.
    # An expired assessment must also become usable again. Merely sending the
    # same expired URL leaves the candidate blocked by the token/start expiry
    # checks even when the email itself is delivered successfully.
    if assessment.status == AssessmentStatus.EXPIRED:
        assessment.status = AssessmentStatus.PENDING
        assessment.expires_at = utcnow() + timedelta(
            days=settings.ASSESSMENT_EXPIRY_DAYS
        )
    assessment.invite_email_send_generation = (
        int(assessment.invite_email_send_generation or 0) + 1
    )
    dispatch_assessment_invite(
        assessment=assessment,
        org=org,
        candidate_email=candidate.email,
        candidate_name=candidate.full_name or candidate.email,
        position=(assessment.task.name if assessment.task else "Technical assessment"),
        pipeline_source=(
            actor.type if actor.type in {"agent", "recruiter"} else "agent"
        ),
        pipeline_actor_type=actor.type,
        pipeline_actor_id=actor.event_actor_id,
        pipeline_reason="Task invite resent",
        pipeline_metadata={"assessment_id": int(assessment.id)},
        pipeline_event_type="assessment_invite_resent",
    )

    return ResendAssessmentInviteResult(
        assessment_id=int(assessment.id), status="queued"
    )
