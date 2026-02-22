"""Synchronous notification helpers (used when Celery is disabled in MVP mode)."""

import logging

from ...platform.config import settings
from .email_client import EmailService

logger = logging.getLogger(__name__)


def send_assessment_invite_sync(
    candidate_email: str,
    candidate_name: str,
    token: str,
    assessment_id: int,
    org_name: str,
    position: str,
) -> None:
    """Send assessment invite email synchronously (MVP mode, no Celery)."""
    if not (settings.RESEND_API_KEY or "").strip():
        return
    email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
    email_svc.send_assessment_invite(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        token=token,
        assessment_id=assessment_id,
        org_name=org_name,
        position=position,
        frontend_url=settings.FRONTEND_URL,
    )


def send_results_notification_sync(
    user_email: str,
    candidate_name: str,
    score: float,
    assessment_id: int,
) -> None:
    """Send results notification email synchronously (MVP mode, no Celery)."""
    if not (settings.RESEND_API_KEY or "").strip():
        return
    email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
    email_svc.send_results_notification(
        user_email=user_email,
        candidate_name=candidate_name,
        score=score,
        assessment_id=assessment_id,
        frontend_url=settings.FRONTEND_URL,
    )


def send_assessment_expiry_reminder_sync(
    candidate_email: str,
    candidate_name: str,
    task_name: str,
    assessment_link: str,
    expiry_text: str,
) -> None:
    """Send reminder for pending assessments nearing expiry."""
    if not (settings.RESEND_API_KEY or "").strip():
        return
    email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
    email_svc.send_assessment_expiry_reminder(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        task_name=task_name,
        assessment_link=assessment_link,
        expiry_text=expiry_text,
    )


def send_candidate_feedback_ready_sync(
    candidate_email: str,
    candidate_name: str,
    org_name: str,
    role_title: str,
    feedback_link: str,
) -> None:
    """Send candidate-facing feedback-ready email."""
    if not (settings.RESEND_API_KEY or "").strip():
        return
    email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
    email_svc.send_candidate_feedback_ready(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        org_name=org_name,
        role_title=role_title,
        feedback_link=feedback_link,
    )
