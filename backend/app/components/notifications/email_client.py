"""
Resend email service for assessment invitations and notifications.

Handles transactional email delivery for candidate invitations and
hiring-manager result notifications via the Resend API.
"""

import logging

import resend

from .templates import assessment_invite_html, results_notification_html, password_reset_html, email_verification_html

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending transactional emails through Resend."""

    def __init__(self, api_key: str, from_email: str = "TALI <noreply@tali.dev>"):
        resend.api_key = api_key
        self.from_email = from_email
        logger.info("EmailService initialised (from=%s)", self.from_email)

    def send_assessment_invite(
        self,
        candidate_email: str,
        candidate_name: str,
        token: str,
        assessment_id: int | None,
        org_name: str,
        position: str,
        frontend_url: str,
    ) -> dict:
        try:
            if assessment_id is not None:
                assessment_link = f"{frontend_url}/assessment/{assessment_id}?token={token}"
            else:
                assessment_link = f"{frontend_url}/assess/{token}"
            logger.info(
                "Sending assessment invite to %s for position '%s' at %s",
                candidate_email, position, org_name,
            )

            html_body = assessment_invite_html(
                candidate_name=candidate_name,
                org_name=org_name,
                position=position,
                assessment_link=assessment_link,
            )

            email = resend.Emails.send({
                "from": self.from_email,
                "to": [candidate_email],
                "subject": f"Technical Assessment Invitation — {position} at {org_name}",
                "html": html_body,
            })

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Assessment invite sent successfully (email_id=%s, to=%s)", email_id, candidate_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send assessment invite to %s: %s", candidate_email, str(e))
            return {"success": False, "email_id": ""}

    def send_results_notification(
        self,
        user_email: str,
        candidate_name: str,
        score: float,
        assessment_id: int,
        frontend_url: str,
    ) -> dict:
        try:
            results_link = f"{frontend_url}/assessments/{assessment_id}"
            logger.info("Sending results notification to %s for candidate '%s'", user_email, candidate_name)

            html_body = results_notification_html(
                candidate_name=candidate_name,
                score=score,
                results_link=results_link,
            )

            email = resend.Emails.send({
                "from": self.from_email,
                "to": [user_email],
                "subject": f"Assessment Complete: {candidate_name} — {score:.0f}%",
                "html": html_body,
            })

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Results notification sent successfully (email_id=%s, to=%s)", email_id, user_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send results notification to %s: %s", user_email, str(e))
            return {"success": False, "email_id": ""}

    def send_email_verification(self, to_email: str, full_name: str, verification_link: str) -> dict:
        try:
            logger.info("Sending email verification to %s", to_email)
            html_body = email_verification_html(full_name=full_name, verification_link=verification_link)

            email = resend.Emails.send({
                "from": self.from_email,
                "to": [to_email],
                "subject": "TALI — Verify your email address",
                "html": html_body,
            })

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Verification email sent (email_id=%s, to=%s)", email_id, to_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send verification email to %s: %s", to_email, str(e))
            return {"success": False, "email_id": ""}

    def send_password_reset(self, to_email: str, reset_link: str) -> dict:
        try:
            logger.info("Sending password reset email to %s", to_email)
            html_body = password_reset_html(reset_link=reset_link)

            email = resend.Emails.send({
                "from": self.from_email,
                "to": [to_email],
                "subject": "TALI — Reset your password",
                "html": html_body,
            })

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Password reset email sent (email_id=%s, to=%s)", email_id, to_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send password reset to %s: %s", to_email, str(e))
            return {"success": False, "email_id": ""}
