"""Resend an existing assessment invite (no new Assessment row).

Used when a candidate didn't receive or lost the original invite. Both
the recruiter UI and the agent invoke this through the same action so
the audit trail and pipeline event are identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ..models.assessment import Assessment, AssessmentStatus
from ..models.organization import Organization
from ..platform.config import settings
from .types import ACTOR_AGENT, ACTOR_SYSTEM, Actor


logger = logging.getLogger("taali.actions.resend_assessment_invite")


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _latest_failed_recipient(assessment: Assessment) -> str | None:
    """Recipient captured with the current bounce/complaint webhook.

    A corrected Candidate email may legitimately receive a fresh invite.  The
    failed address may not.  When an older webhook omitted ``data.to`` we fail
    closed because there is no durable evidence that the address was changed.
    """

    for event in reversed(list(assessment.timeline or [])):
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "assessment_invite_delivery_failed":
            continue
        recipient = _normalized_email(event.get("recipient"))
        return recipient or None
    return None


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
        block_reason = automatic_role_action_block_reason(role)
        if block_reason:
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=f"Automatic invite resend held: {block_reason}",
            )
        from ..services.agent_policy_settings import (
            automation_enabled_for_decision,
        )

        if not automation_enabled_for_decision(
            role, "resend_assessment_invite"
        ):
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=(
                    "Automatic invite resend held: "
                    "role.auto_resend_assessment is not enabled"
                ),
            )
        configured_allowlist = getattr(role, "agent_action_allowlist", None)
        if configured_allowlist is not None and (
            not isinstance(configured_allowlist, (list, tuple, set, frozenset))
            or "resend_assessment_invite"
            not in {str(value).strip() for value in configured_allowlist}
        ):
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=(
                    "Automatic invite resend held: action is not in the role "
                    "agent allowlist"
                ),
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

    candidate_email = _normalized_email(candidate.email)
    from ..services.email_suppression_service import is_suppressed

    suppression_reason = is_suppressed(
        db,
        email=candidate_email,
        organization_id=int(organization_id),
    )
    if suppression_reason:
        return ResendAssessmentInviteResult(
            assessment_id=int(assessment.id),
            status="blocked",
            detail=(
                "Invite resend held: candidate email is suppressed "
                f"({suppression_reason}). Verify or correct the address first."
            ),
        )

    delivery_status = str(assessment.invite_email_status or "").strip().lower()
    if delivery_status in {"bounced", "complained"}:
        failed_recipient = _latest_failed_recipient(assessment)
        if failed_recipient is None or failed_recipient == candidate_email:
            return ResendAssessmentInviteResult(
                assessment_id=int(assessment.id),
                status="blocked",
                detail=(
                    "Invite resend held after a hard delivery failure. Correct "
                    "the candidate email before sending another invite."
                ),
            )

    if assessment.status in {
        AssessmentStatus.IN_PROGRESS,
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }:
        return ResendAssessmentInviteResult(
            assessment_id=int(assessment.id),
            status="blocked",
            detail=f"Assessment is {assessment.status.value} and cannot be resent",
        )

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    # A resend is a fresh candidate window.  This is essential for expiry
    # recovery: emailing the old token without moving the absolute expiry would
    # deliver a link that immediately returns 400.  Pending lost-email and
    # corrected-bounce resends receive the same predictable full window.
    assessment.status = AssessmentStatus.PENDING
    assessment.expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.ASSESSMENT_EXPIRY_DAYS
    )

    # Automatic recovery must retain one stable provider idempotency key, but
    # an explicit resend is a new logical candidate email.  Advance the durable
    # generation before registering the outbox intent so every retry of this
    # resend shares a new key without being collapsed into the original send.
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
