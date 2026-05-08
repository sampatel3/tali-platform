"""Dispatch the assessment invite to the candidate.

Behavior (post-2026-05-07 restructure):

1. Always send the Taali-branded assessment email. Only Taali knows the
   per-candidate assessment URL + token, so the email has to come from
   Taali regardless of the org's Workable wiring.
2. If the org runs in ``workable_hybrid`` mode AND has the Workable
   credentials and ``invite_stage_name`` configured AND the candidate
   has a ``workable_candidate_id``, ALSO move them to the configured
   Workable stage and post an activity note. This keeps the recruiter's
   Workable view in sync without relying on Workable's stage-driven
   email automation (which can't include Taali's unique link anyway).

Workable failures are non-fatal — they're logged and the email already
went out. ``assessment.invite_channel`` records what actually happened:

  - ``"manual"``        → email sent, Workable not attempted
  - ``"workable_hybrid"`` → email sent + Workable updated successfully
  - ``"workable_partial"`` → email sent but the Workable update failed

The legacy ``email_mode`` config field is still honored: ``manual_taali``
suppresses the Workable-side action even when the org is connected.
``workable_preferred_fallback_manual`` is treated as opt-in to the
Workable update.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...components.notifications.service import send_assessment_invite_sync
from ...models.assessment import Assessment
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.request_context import get_request_id
from .adapters import build_workable_adapter
from ...services.workable_actions_service import move_candidate_in_workable

logger = logging.getLogger(__name__)


def _workable_config(org: Organization) -> dict:
    config = org.workable_config if isinstance(org.workable_config, dict) else {}
    return {
        "email_mode": str(config.get("email_mode") or "manual_taali"),
        "workflow_mode": str(config.get("workflow_mode") or "manual"),
        "invite_stage_name": str(config.get("invite_stage_name") or "").strip(),
    }


def _resolve_candidate_facing_brand(org: Organization) -> str | None:
    """Pull candidate_facing_brand from the org's workspace_settings JSON.

    Falls back to ``None`` when not set so the EmailService can use
    ``org_name`` as the next-best display name. Stripped + truncated to
    avoid weird inbox display.
    """
    settings_json = org.workspace_settings if isinstance(org.workspace_settings, dict) else {}
    raw = str(settings_json.get("candidate_facing_brand") or "").strip()
    return raw[:200] or None


def _send_taali_invite_email(
    *,
    candidate_email: str,
    candidate_name: str,
    token: str,
    assessment_id: int,
    org_name: str,
    position: str,
    candidate_facing_brand: str | None,
    reply_to: str | None,
) -> None:
    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment_id,
            org_name=org_name,
            position=position,
            candidate_facing_brand=candidate_facing_brand,
            reply_to=reply_to,
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
        candidate_facing_brand=candidate_facing_brand,
        reply_to=reply_to,
    )


def _workable_handoff_eligible(*, assessment: Assessment, org: Organization, config: dict) -> bool:
    """Should we ALSO update Workable in addition to sending the Taali email?"""
    if settings.MVP_DISABLE_WORKABLE:
        return False
    if not (
        org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
    ):
        return False
    if not assessment.workable_candidate_id:
        return False
    if not config["invite_stage_name"]:
        return False
    # Honor explicit opt-out via legacy ``manual_taali`` config — even if
    # everything else is wired, the recruiter has said "no Workable side
    # effects on assessment send".
    if config["email_mode"] == "manual_taali":
        return False
    return True


def _do_workable_handoff(
    *,
    assessment: Assessment,
    org: Organization,
    candidate_email: str,
    candidate_name: str,
    stage_name: str,
) -> bool:
    """Move the candidate in Workable + post an activity note. Returns
    True on full success, False on any failure (caller logs + records)."""
    try:
        assessment_link = (
            f"{settings.FRONTEND_URL}/assessment/{assessment.id}?token={assessment.token}"
        )
        activity = (
            "TAALI assessment invite sent.\n\n"
            f"Candidate: {candidate_name} <{candidate_email}>\n"
            f"Assessment link: {assessment_link}\n"
        )
        stage_result = move_candidate_in_workable(
            org=org,
            candidate_id=assessment.workable_candidate_id,
            target_stage=stage_name,
        )
        if not stage_result.get("success"):
            logger.warning(
                "workable stage move failed assessment_id=%s code=%s message=%s",
                assessment.id,
                stage_result.get("code"),
                stage_result.get("message"),
            )
            return False
        adapter = build_workable_adapter(
            access_token=org.workable_access_token,
            subdomain=org.workable_subdomain,
        )
        activity_result = adapter.post_candidate_activity(
            assessment.workable_candidate_id, activity
        )
        if not activity_result.get("success"):
            logger.warning(
                "workable activity-note post failed assessment_id=%s",
                assessment.id,
            )
            return False
        return True
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable assessment-handoff raised unexpectedly assessment_id=%s",
            assessment.id,
        )
        return False


def dispatch_assessment_invite(
    *,
    assessment: Assessment,
    org: Organization,
    candidate_email: str,
    candidate_name: str,
    position: str,
    reply_to: str | None = None,
) -> str:
    """Send the Taali assessment email; optionally update Workable too.

    Returns the resolved ``invite_channel`` ("manual",
    "workable_hybrid", or "workable_partial") and stamps it on the
    assessment along with ``invite_sent_at``.

    ``reply_to``: candidate replies route here (typically the recruiter's
    email). When None, replies hit the platform's no-reply address — fine
    for fully autonomous agent sends, less ideal for recruiter-triggered
    sends where the recruiter wants to handle responses themselves.
    """
    config = _workable_config(org)
    candidate_facing_brand = _resolve_candidate_facing_brand(org)

    # Step 1: always send the Taali email (it has the unique link).
    _send_taali_invite_email(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        token=assessment.token,
        assessment_id=assessment.id,
        org_name=org.name if org else "Your recruiter",
        position=position,
        candidate_facing_brand=candidate_facing_brand,
        reply_to=reply_to,
    )

    # Step 2: optionally update Workable in addition.
    workable_status = "skipped"
    if _workable_handoff_eligible(assessment=assessment, org=org, config=config):
        ok = _do_workable_handoff(
            assessment=assessment,
            org=org,
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            stage_name=config["invite_stage_name"],
        )
        workable_status = "ok" if ok else "failed"

    if workable_status == "ok":
        channel = "workable_hybrid"
    elif workable_status == "failed":
        channel = "workable_partial"
    else:
        channel = "manual"

    assessment.invite_channel = channel
    assessment.invite_sent_at = datetime.now(timezone.utc)
    return channel
