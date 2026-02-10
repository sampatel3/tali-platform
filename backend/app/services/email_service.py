"""
Resend email service for assessment invitations and notifications.

Handles transactional email delivery for candidate invitations and
hiring-manager result notifications via the Resend API.
"""

import logging

import resend

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending transactional emails through Resend."""

    def __init__(self, api_key: str, from_email: str = "TALI <noreply@tali.dev>"):
        """
        Initialise the email service.

        Args:
            api_key: Resend API key.
            from_email: Default sender address with display name.
        """
        resend.api_key = api_key
        self.from_email = from_email
        logger.info("EmailService initialised (from=%s)", self.from_email)

    def send_assessment_invite(
        self,
        candidate_email: str,
        candidate_name: str,
        token: str,
        org_name: str,
        position: str,
        frontend_url: str,
    ) -> dict:
        """
        Send an assessment invitation email to a candidate.

        Args:
            candidate_email: Recipient email address.
            candidate_name: Candidate's display name.
            token: Unique assessment access token.
            org_name: Name of the hiring organisation.
            position: Job title / position name.
            frontend_url: Base URL of the frontend application.

        Returns:
            Dict with keys: success, email_id.
        """
        try:
            assessment_link = f"{frontend_url}/assess/{token}"
            logger.info(
                "Sending assessment invite to %s for position '%s' at %s",
                candidate_email,
                position,
                org_name,
            )

            html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <!-- Header -->
          <tr>
            <td style="background-color:#6366f1;padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">TALI</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">Technical Assessment Platform</p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Hi {candidate_name},</h2>
              <p style="margin:0 0 16px;color:#4b5563;font-size:16px;line-height:1.6;">
                You have been invited by <strong>{org_name}</strong> to complete a technical assessment
                for the <strong>{position}</strong> role.
              </p>
              <p style="margin:0 0 24px;color:#4b5563;font-size:16px;line-height:1.6;">
                Click the button below to begin. You will have access to a live coding environment
                with AI-assisted debugging support.
              </p>
              <!-- CTA Button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td style="background-color:#6366f1;border-radius:6px;text-align:center;">
                    <a href="{assessment_link}"
                       style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">
                      Start Assessment
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px;color:#9ca3af;font-size:13px;">Or copy this link into your browser:</p>
              <p style="margin:0 0 24px;color:#6366f1;font-size:13px;word-break:break-all;">{assessment_link}</p>
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
              <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;">
                This invitation was sent via TALI on behalf of {org_name}.<br>
                If you did not expect this email, you can safely ignore it.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

            email = resend.Emails.send(
                {
                    "from": self.from_email,
                    "to": [candidate_email],
                    "subject": f"Technical Assessment Invitation — {position} at {org_name}",
                    "html": html_body,
                }
            )

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info(
                "Assessment invite sent successfully (email_id=%s, to=%s)",
                email_id,
                candidate_email,
            )

            return {
                "success": True,
                "email_id": email_id,
            }
        except Exception as e:
            logger.error(
                "Failed to send assessment invite to %s: %s",
                candidate_email,
                str(e),
            )
            return {
                "success": False,
                "email_id": "",
            }

    def send_results_notification(
        self,
        user_email: str,
        candidate_name: str,
        score: float,
        assessment_id: int,
        frontend_url: str,
    ) -> dict:
        """
        Notify a hiring manager that an assessment has been completed.

        Args:
            user_email: Hiring manager's email address.
            candidate_name: Name of the candidate who completed the assessment.
            score: Assessment score (0–100).
            assessment_id: Internal assessment ID for linking to results.
            frontend_url: Base URL of the frontend application.

        Returns:
            Dict with keys: success, email_id.
        """
        try:
            results_link = f"{frontend_url}/assessments/{assessment_id}"
            logger.info(
                "Sending results notification to %s for candidate '%s'",
                user_email,
                candidate_name,
            )

            # Determine score colour for visual emphasis
            if score >= 70:
                score_colour = "#16a34a"  # green
            elif score >= 40:
                score_colour = "#d97706"  # amber
            else:
                score_colour = "#dc2626"  # red

            html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <!-- Header -->
          <tr>
            <td style="background-color:#6366f1;padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">TALI</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">Technical Assessment Platform</p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Assessment Completed</h2>
              <p style="margin:0 0 16px;color:#4b5563;font-size:16px;line-height:1.6;">
                <strong>{candidate_name}</strong> has completed their technical assessment.
              </p>
              <!-- Score card -->
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;background-color:#f9fafb;border-radius:8px;border:1px solid #e5e7eb;">
                <tr>
                  <td style="padding:24px;text-align:center;">
                    <p style="margin:0 0 4px;color:#6b7280;font-size:14px;text-transform:uppercase;letter-spacing:0.5px;">Score</p>
                    <p style="margin:0;color:{score_colour};font-size:36px;font-weight:700;">{score:.0f}%</p>
                  </td>
                </tr>
              </table>
              <!-- CTA Button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td style="background-color:#6366f1;border-radius:6px;text-align:center;">
                    <a href="{results_link}"
                       style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">
                      View Full Results
                    </a>
                  </td>
                </tr>
              </table>
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
              <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;">
                This notification was sent by TALI. You are receiving this because
                you are listed as a reviewer for this assessment.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

            email = resend.Emails.send(
                {
                    "from": self.from_email,
                    "to": [user_email],
                    "subject": f"Assessment Complete: {candidate_name} — {score:.0f}%",
                    "html": html_body,
                }
            )

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info(
                "Results notification sent successfully (email_id=%s, to=%s)",
                email_id,
                user_email,
            )

            return {
                "success": True,
                "email_id": email_id,
            }
        except Exception as e:
            logger.error(
                "Failed to send results notification to %s: %s",
                user_email,
                str(e),
            )
            return {
                "success": False,
                "email_id": "",
            }
