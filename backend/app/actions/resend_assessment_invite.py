"""Resend an existing assessment invite (no new Assessment row).

Used when a candidate didn't receive or lost the original invite. Both
the recruiter UI and the agent invoke this through the same action so
the audit trail and pipeline event are identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
)
from ..domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from .types import Actor


logger = logging.getLogger("taali.actions.resend_assessment_invite")


@dataclass(frozen=True)
class ResendAssessmentInviteResult:
    assessment_id: int
    status: str  # "resent" | "voided" | "no_candidate"
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

    dispatch_assessment_invite(
        assessment=assessment,
        org=org,
        candidate_email=candidate.email,
        candidate_name=candidate.full_name or candidate.email,
        position=(assessment.task.name if assessment.task else "Technical assessment"),
    )

    if assessment.application_id:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == assessment.application_id,
                CandidateApplication.organization_id == organization_id,
            )
            .first()
        )
        if app is not None:
            ensure_pipeline_fields(app)
            initialize_pipeline_event_if_missing(
                db,
                app=app,
                actor_type="system",
                actor_id=actor.event_actor_id,
                reason="Pipeline initialized before invite resend",
            )
            append_application_event(
                db,
                app=app,
                event_type="assessment_invite_resent",
                actor_type=actor.type,
                actor_id=actor.event_actor_id,
                reason="Task invite resent",
                metadata={"assessment_id": int(assessment.id)},
            )

    return ResendAssessmentInviteResult(
        assessment_id=int(assessment.id), status="resent"
    )
