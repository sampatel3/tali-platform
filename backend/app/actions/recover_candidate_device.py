"""Move an in-progress assessment to a replacement browser securely."""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from sqlalchemy import null
from sqlalchemy.orm import Session

from ..components.assessments.repository import append_assessment_timeline_event
from ..models.assessment import (
    Assessment,
    AssessmentStatus,
    CandidateAssessmentProofNonce,
)
from .resend_assessment_invite import run as resend_invite
from .types import Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    assessment_id: int,
) -> dict[str, object]:
    """Rotate browser access, preserving the existing sandbox and timer."""
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == int(organization_id),
        )
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="Device recovery is available only while an assessment is in progress",
        )
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if getattr(assessment, "runtime_operation_id", None):
        raise HTTPException(
            status_code=409,
            detail="The workspace is finishing an operation. Try device recovery again in a moment.",
        )
    candidate = getattr(assessment, "candidate", None)
    if candidate is None or not str(getattr(candidate, "email", "") or "").strip():
        raise HTTPException(status_code=400, detail="Assessment has no candidate email")

    previous_key_id = str(getattr(assessment, "candidate_proof_key_id", "") or "")
    assessment.token = secrets.token_urlsafe(32)
    assessment.candidate_session_hash = None
    assessment.candidate_session_bound_at = None
    assessment.candidate_proof_key_id = None
    # JSON ``None`` serializes as JSON null; this constraint requires SQL NULL.
    assessment.candidate_proof_public_jwk = null()
    assessment.candidate_proof_key_bound_at = None
    db.query(CandidateAssessmentProofNonce).filter(
        CandidateAssessmentProofNonce.assessment_id == assessment.id,
    ).delete(synchronize_session=False)
    append_assessment_timeline_event(
        assessment,
        "candidate_device_recovery_requested",
        {
            "actor_user_id": actor.event_actor_id,
            "replaced_bound_key": bool(previous_key_id),
            "workspace_preserved": True,
        },
    )
    db.flush()

    result = resend_invite(
        db,
        actor,
        organization_id=int(organization_id),
        assessment_id=int(assessment.id),
    )
    if result.status != "queued":
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=result.detail or "Could not send recovery link",
        )
    db.commit()
    return {
        "success": True,
        "workspace_preserved": True,
        "message": "A new secure link was sent. The candidate will resume the same workspace.",
    }
