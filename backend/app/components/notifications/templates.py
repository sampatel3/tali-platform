"""HTML email templates for TALI notifications."""


def assessment_invite_html(
    candidate_name: str,
    org_name: str,
    position: str,
    assessment_link: str,
) -> str:
    return f"""\
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
          <tr>
            <td style="background-color:#6366f1;padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">TALI</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">Technical Assessment Platform</p>
            </td>
          </tr>
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


def results_notification_html(
    candidate_name: str,
    score: float,
    results_link: str,
) -> str:
    if score >= 70:
        score_colour = "#16a34a"
    elif score >= 40:
        score_colour = "#d97706"
    else:
        score_colour = "#dc2626"

    return f"""\
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
          <tr>
            <td style="background-color:#6366f1;padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">TALI</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">Technical Assessment Platform</p>
            </td>
          </tr>
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Assessment Completed</h2>
              <p style="margin:0 0 16px;color:#4b5563;font-size:16px;line-height:1.6;">
                <strong>{candidate_name}</strong> has completed their technical assessment.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;background-color:#f9fafb;border-radius:8px;border:1px solid #e5e7eb;">
                <tr>
                  <td style="padding:24px;text-align:center;">
                    <p style="margin:0 0 4px;color:#6b7280;font-size:14px;text-transform:uppercase;letter-spacing:0.5px;">Score</p>
                    <p style="margin:0;color:{score_colour};font-size:36px;font-weight:700;">{score:.0f}%</p>
                  </td>
                </tr>
              </table>
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


def email_verification_html(full_name: str, verification_link: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background-color:#6366f1;padding:32px;text-align:center;">
          <h1 style="margin:0;color:#ffffff;font-size:28px;">TALI</h1>
          <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">Technical Assessment Platform</p>
        </td></tr>
        <tr><td style="padding:40px;">
          <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Verify your email</h2>
          <p style="margin:0 0 8px;color:#4b5563;font-size:16px;line-height:1.6;">
            Hi {full_name},
          </p>
          <p style="margin:0 0 24px;color:#4b5563;font-size:16px;line-height:1.6;">
            Thanks for signing up for TALI. Please verify your email address by clicking the button below.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;"><tr><td style="background-color:#6366f1;border-radius:6px;text-align:center;">
            <a href="{verification_link}" style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">Verify Email</a>
          </td></tr></table>
          <p style="margin:0 0 8px;color:#9ca3af;font-size:13px;">Or copy this link into your browser:</p>
          <p style="margin:0 0 24px;color:#6366f1;font-size:13px;word-break:break-all;">{verification_link}</p>
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
          <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;">
            This link expires in 24 hours. If you didn't create a TALI account, you can ignore this email.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def password_reset_html(reset_link: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background-color:#6366f1;padding:32px;text-align:center;">
          <h1 style="margin:0;color:#ffffff;font-size:28px;">TALI</h1>
        </td></tr>
        <tr><td style="padding:40px;">
          <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Reset your password</h2>
          <p style="margin:0 0 24px;color:#4b5563;font-size:16px;line-height:1.6;">
            Click the button below to set a new password. This link expires in 1 hour.
          </p>
          <table cellpadding="0" cellspacing="0"><tr><td style="background-color:#6366f1;border-radius:6px;text-align:center;">
            <a href="{reset_link}" style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">Reset password</a>
          </td></tr></table>
          <p style="margin:24px 0 0;color:#9ca3af;font-size:13px;">If you didn't request this, you can ignore this email.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
