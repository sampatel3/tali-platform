from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...components.notifications.service import send_assessment_invite_sync
from ...models.assessment import Assessment
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.request_context import get_request_id
from .adapters import build_workable_adapter

logger = logging.getLogger(__name__)


def _workable_config(org: Organization) -> dict:
    config = org.workable_config if isinstance(org.workable_config, dict) else {}
    return {
        "email_mode": str(config.get("email_mode") or "manual_taali"),
        "invite_stage_name": str(config.get("invite_stage_name") or "").strip(),
    }


def _send_manual_invite(
    *,
    candidate_email: str,
    candidate_name: str,
    token: str,
    assessment_id: int,
    org_name: str,
    position: str,
) -> None:
    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment_id,
            org_name=org_name,
            position=position,
        )
        return
    from ...tasks.assessment_tasks import send_assessment_email

    send_assessment_email.delay(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        token=token,
        org_name=org_name,
        position=position,
        assessment_id=assessment_id,
        request_id=get_request_id(),
    )


def dispatch_assessment_invite(
    *,
    assessment: Assessment,
    org: Organization,
    candidate_email: str,
    candidate_name: str,
    position: str,
) -> str:
    """Dispatch invite via Workable-first hybrid flow with manual fallback."""
    config = _workable_config(org)
    email_mode = config["email_mode"]
    stage_name = config["invite_stage_name"]
    attempted_workable = False

    if (
        email_mode == "workable_preferred_fallback_manual"
        and bool(stage_name)
        and not settings.MVP_DISABLE_WORKABLE
        and org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
        and assessment.workable_candidate_id
    ):
        attempted_workable = True
        try:
            adapter = build_workable_adapter(
                access_token=org.workable_access_token,
                subdomain=org.workable_subdomain,
            )
            assessment_link = f"{settings.FRONTEND_URL}/assessment/{assessment.id}?token={assessment.token}"
            activity = (
                "TAALI assessment invite generated.\n\n"
                f"Candidate: {candidate_name} <{candidate_email}>\n"
                f"Assessment link: {assessment_link}\n"
            )
            stage_result = adapter.update_candidate_stage(assessment.workable_candidate_id, stage_name)
            activity_result = adapter.post_candidate_activity(assessment.workable_candidate_id, activity)
            if stage_result.get("success") and activity_result.get("success"):
                assessment.invite_channel = "workable"
                assessment.invite_sent_at = datetime.now(timezone.utc)
                return "workable"
        except Exception:
            logger.exception("Workable invite dispatch failed; falling back to manual invite")

    _send_manual_invite(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        token=assessment.token,
        assessment_id=assessment.id,
        org_name=org.name if org else "Your recruiter",
        position=position,
    )
    assessment.invite_channel = "manual_fallback" if attempted_workable else "manual"
    assessment.invite_sent_at = datetime.now(timezone.utc)
    return assessment.invite_channel
