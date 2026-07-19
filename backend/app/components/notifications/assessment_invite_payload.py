"""Detached payload loading for Assessment-backed invitation delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import joinedload

from ...models.assessment import Assessment
from ...platform.database import SessionLocal


@dataclass(frozen=True)
class AssessmentInvitePayload:
    candidate_email: str
    candidate_name: str
    token: str
    org_name: str
    position: str
    candidate_facing_brand: str | None
    reply_to: str | None

    @property
    def core(self) -> tuple[str, str, str, str, str]:
        return (
            self.candidate_email,
            self.candidate_name,
            self.token,
            self.org_name,
            self.position,
        )

    def brand_and_reply_to(
        self, requested_reply_to: str | None
    ) -> tuple[str | None, str | None]:
        return self.candidate_facing_brand, requested_reply_to or self.reply_to


def load_assessment_invite_payload(
    assessment_id: int,
) -> AssessmentInvitePayload | None:
    """Read the canonical invite fields, then close SQL before provider I/O."""
    db = SessionLocal()
    try:
        assessment = (
            db.query(Assessment)
            .options(
                joinedload(Assessment.candidate),
                joinedload(Assessment.task),
                joinedload(Assessment.organization),
            )
            .filter(Assessment.id == int(assessment_id))
            .one_or_none()
        )
        if assessment is None:
            return None
        candidate = assessment.candidate
        organization = assessment.organization
        email = str(getattr(candidate, "email", "") or "").strip()
        token = str(assessment.token or "").strip()
        if candidate is None or organization is None or not email or not token:
            return None
        workspace_settings = (
            organization.workspace_settings
            if isinstance(organization.workspace_settings, dict)
            else {}
        )
        brand = str(workspace_settings.get("candidate_facing_brand") or "").strip()
        return AssessmentInvitePayload(
            candidate_email=email,
            candidate_name=str(candidate.full_name or email),
            token=token,
            org_name=str(organization.name or "Your recruiter"),
            position=str(
                assessment.task.name
                if assessment.task is not None
                else "Technical assessment"
            ),
            candidate_facing_brand=brand[:200] or None,
            reply_to=(str(assessment.invite_email_reply_to or "").strip() or None),
        )
    finally:
        db.close()


def load_invite_payload_or_mark_failed(
    assessment_id: int,
    *,
    generation: int,
    log_extra: dict,
    persist: Callable[..., bool],
) -> AssessmentInvitePayload | None:
    """Load the detached payload and terminally surface corrupted source rows."""
    payload = load_assessment_invite_payload(int(assessment_id))
    if payload is None:
        persist(
            int(assessment_id),
            status="dispatch_failed",
            claimed_at=None,
            last_error="persisted invite delivery payload is missing",
            expected_generation=int(generation),
            log_extra=log_extra,
        )
    return payload


__all__ = [
    "AssessmentInvitePayload",
    "load_assessment_invite_payload",
    "load_invite_payload_or_mark_failed",
]
