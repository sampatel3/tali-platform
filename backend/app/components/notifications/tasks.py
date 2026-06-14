"""Celery email tasks (canonical location)."""

import logging
from ...tasks.celery_app import celery_app
from ...platform.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_assessment_email(
    self,
    candidate_email: str,
    candidate_name: str,
    token: str,
    org_name: str,
    position: str,
    assessment_id: int | None = None,
    candidate_facing_brand: str | None = None,
    reply_to: str | None = None,
    request_id: str | None = None,
):
    """Send assessment invitation email to candidate."""
    from .email_client import EmailService

    log_extra = {"request_id": request_id or self.request.id}
    if not (settings.RESEND_API_KEY or "").strip():
        logger.info(f"RESEND_API_KEY not set — skipping assessment email to {candidate_email}", extra=log_extra)
        return {"success": False, "skipped": True}
    try:
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        result = email_svc.send_assessment_invite(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment_id,
            org_name=org_name,
            position=position,
            frontend_url=settings.FRONTEND_URL,
            candidate_facing_brand=candidate_facing_brand,
            reply_to=reply_to,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(f"Assessment email sent to {candidate_email}", extra=log_extra)
        return result
    except Exception as exc:
        logger.error(f"Failed to send assessment email to {candidate_email}: {exc}", extra=log_extra)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_results_email(self, user_email: str, candidate_name: str, score: float, assessment_id: int):
    """Notify hiring manager that assessment is complete."""
    from .email_client import EmailService

    if not (settings.RESEND_API_KEY or "").strip():
        logger.info(f"RESEND_API_KEY not set — skipping results email to {user_email}")
        return {"success": False, "skipped": True}
    try:
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        result = email_svc.send_results_notification(
            user_email=user_email,
            candidate_name=candidate_name,
            score=score,
            assessment_id=assessment_id,
            frontend_url=settings.FRONTEND_URL,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(f"Results email sent to {user_email} for assessment {assessment_id}")
        return result
    except Exception as exc:
        logger.error(f"Failed to send results email: {exc}")
        raise self.retry(exc=exc)
