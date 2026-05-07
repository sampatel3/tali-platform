"""HTML email templates for platform notifications."""

from html import escape as _html_escape
from string import Template as _StringTemplate

from ...platform.brand import BRAND_NAME, BRAND_PRODUCT_NAME


def _h(value: str | None) -> str:
    """HTML-escape a value for safe interpolation in templates.

    Org names like ``"Acme & Co"`` or candidate names containing ``<``
    would otherwise corrupt the rendered email. ``None`` is rendered as
    an empty string so optional fields don't print "None".
    """
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


def application_rejected_html(
    candidate_name: str,
    org_name: str,
    position: str,
) -> str:
    """Brief, professional candidate-rejection email.

    Used when a recruiter rejects an application — including via approving
    an agent-queued ``reject`` decision. We keep the body short and avoid
    detailed feedback by default; orgs that want to share scores or
    excerpts can do so manually before clicking approve.
    """
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
            <td style="background-color:#1f2937;padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">{org_name}</h1>
              <p style="margin:6px 0 0;color:#9ca3af;font-size:13px;">Application update</p>
            </td>
          </tr>
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 16px;color:#1f2937;font-size:20px;">Hi {candidate_name},</h2>
              <p style="margin:0 0 16px;color:#4b5563;font-size:16px;line-height:1.6;">
                Thank you for your interest in the <strong>{position}</strong> role at
                <strong>{org_name}</strong>, and for the time you put into your application.
              </p>
              <p style="margin:0 0 16px;color:#4b5563;font-size:16px;line-height:1.6;">
                After careful review we&#39;ve decided not to move forward with your
                application at this time. We received many strong submissions, and
                this decision is in no way a reflection on the quality of your work.
              </p>
              <p style="margin:0 0 24px;color:#4b5563;font-size:16px;line-height:1.6;">
                We&#39;ll keep your details on file and reach out if a future opening
                looks like a better fit. We wish you the best in your search.
              </p>
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
              <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;">
                Sent via {BRAND_NAME} on behalf of {org_name}.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def assessment_invite_text(
    candidate_name: str,
    org_name: str,
    position: str,
    assessment_link: str,
) -> str:
    """Plain-text companion for ``assessment_invite_html``.

    Sent in the ``text`` field of the Resend payload alongside the HTML.
    Used by:
    - Inbox preview (some clients prefer text over HTML for the snippet)
    - Accessibility (screen readers)
    - Plain-text-only mail clients

    Mirrors the visible HTML body. No escaping needed (plain text).
    Body matches the designer-handoff source at
    ``emails/assessment_invite.txt`` from the Taali Design System bundle.
    """
    cand = (candidate_name or "there").strip() or "there"
    org = (org_name or "the hiring team").strip() or "the hiring team"
    role = (position or "the role").strip() or "the role"
    link = (assessment_link or "").strip()
    return (
        f"Hi {cand},\n\n"
        f"Thanks for applying to the {role} role at {org}. As part of\n"
        f"next steps, please complete a short technical assessment.\n\n"
        f"Start the assessment:\n"
        f"{link}\n\n"
        f"What to expect\n"
        f"- In-browser coding: live editor and terminal, nothing to install.\n"
        f"- AI-assisted: use the built-in chat to debug and reason out loud.\n"
        f"- About 60 minutes, in one sitting, when it suits you.\n\n"
        f"The link above is unique to you -- please don't forward or share it. If you\n"
        f"didn't apply to {org}, you can safely ignore this email.\n\n"
        f"Good luck,\n"
        f"The {org} hiring team\n\n"
        f"---\n"
        f"Sent on behalf of {org} via Taali (https://taali.ai), their\n"
        f"assessment platform. If you didn't apply, you can ignore this email -- the\n"
        f"link won't be reused.\n"
    )


# Designer-handoff source: ``emails/assessment_invite.html`` (Taali Design
# System, 2026-05-07). Stored as a ``string.Template`` so the embedded
# ``<style>`` block's CSS braces don't conflict with f-string / .format()
# substitution. Variables: ``${candidate_name}``, ``${org_name}``,
# ``${position}``, ``${assessment_link}`` — all HTML-escaped before
# substitution by ``assessment_invite_html`` below.
_ASSESSMENT_INVITE_HTML_TEMPLATE = _StringTemplate("""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="color-scheme" content="light only" />
  <meta name="supported-color-schemes" content="light only" />
  <title>${org_name} — Technical assessment for ${position}</title>
  <!--[if mso]>
  <style type="text/css">
    table, td, div, h1, h2, p { font-family: Arial, Helvetica, sans-serif !important; }
    .btn-cta a { padding: 14px 28px !important; }
  </style>
  <![endif]-->
  <style type="text/css">
    /* Client resets */
    body { margin: 0 !important; padding: 0 !important; width: 100% !important; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
    table { border-collapse: collapse !important; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
    img { border: 0; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }
    a { text-decoration: none; }
    /* Mobile */
    @media screen and (max-width: 620px) {
      .container { width: 100% !important; }
      .px-32 { padding-left: 24px !important; padding-right: 24px !important; }
      .px-40 { padding-left: 24px !important; padding-right: 24px !important; }
      .h-display { font-size: 22px !important; line-height: 1.25 !important; }
      .body-lg { font-size: 16px !important; line-height: 1.55 !important; }
      .btn-cta a { display: block !important; width: 100% !important; box-sizing: border-box !important; text-align: center !important; }
      .stack-row td { display: block !important; width: 100% !important; padding-bottom: 12px !important; }
      .stack-row td.last { padding-bottom: 0 !important; }
    }
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f4f3f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1730;">

  <!-- Preheader: shown in inbox-preview line, hidden in body. -->
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f4f3f0;opacity:0;">
    Your ${position} assessment at ${org_name} — Thanks for applying. Plan ~60 minutes; the link is unique to you.
    &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp;
  </div>

  <!-- Outer wrapper -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f3f0;">
    <tr>
      <td align="center" style="padding:24px 12px;">

        <!-- 600px container -->
        <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background-color:#ffffff;border:1px solid #e7e0f0;border-radius:14px;overflow:hidden;">

          <!-- Co-brand header bar -->
          <tr>
            <td class="px-32" style="padding:22px 32px;border-bottom:1px solid #efe8f7;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="left" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#6e6580;">
                    Technical&nbsp;assessment
                  </td>
                  <td align="right" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#9e96ae;">
                    Next&nbsp;step
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Org name as the brand on display -->
          <tr>
            <td class="px-40" style="padding:36px 40px 8px 40px;">
              <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;font-weight:500;color:#6e6580;letter-spacing:-0.005em;">
                ${org_name}
              </div>
            </td>
          </tr>

          <!-- Headline: leads with the role at the company -->
          <tr>
            <td class="px-40" style="padding:6px 40px 18px 40px;">
              <h1 class="h-display" style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.22;font-weight:600;letter-spacing:-0.02em;color:#1d1730;">
                Your ${position} assessment is ready.
              </h1>
            </td>
          </tr>

          <!-- Body intro -->
          <tr>
            <td class="px-40" style="padding:8px 40px 4px 40px;">
              <p class="body-lg" style="margin:0 0 16px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.6;color:#2e2745;">
                Hi ${candidate_name},
              </p>
              <p class="body-lg" style="margin:0 0 16px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.6;color:#2e2745;">
                Thanks for applying to the <strong style="color:#1d1730;font-weight:600;">${position}</strong> role at <strong style="color:#1d1730;font-weight:600;">${org_name}</strong>. As part of next steps, please complete a short technical assessment.
              </p>
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td class="px-40" style="padding:18px 40px 8px 40px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="btn-cta">
                <tr>
                  <td bgcolor="#1d1730" style="border-radius:999px;mso-padding-alt:14px 28px;">
                    <!--[if mso]>&nbsp;<![endif]-->
                    <a href="${assessment_link}" target="_blank" style="display:inline-block;padding:14px 28px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;line-height:1;color:#ffffff;background-color:#1d1730;border-radius:999px;letter-spacing:-0.005em;">
                      Start assessment&nbsp;&rarr;
                    </a>
                    <!--[if mso]>&nbsp;<![endif]-->
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Plain-text fallback link -->
          <tr>
            <td class="px-40" style="padding:14px 40px 8px 40px;">
              <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.55;color:#6e6580;">
                Button not working? Paste this into your browser:<br />
                <a href="${assessment_link}" target="_blank" style="color:#6b21e8;text-decoration:underline;word-break:break-all;">${assessment_link}</a>
              </p>
            </td>
          </tr>

          <!-- What to expect — 3 quick facts, table-laid -->
          <tr>
            <td class="px-40" style="padding:24px 40px 8px 40px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #efe8f7;">
                <tr>
                  <td style="padding-top:22px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#6e6580;">
                    What to expect
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td class="px-40" style="padding:14px 40px 8px 40px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="stack-row">
                <tr>
                  <td valign="top" width="33%" style="padding-right:14px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
                    <div style="font-size:13px;font-weight:600;color:#1d1730;line-height:1.35;letter-spacing:-0.005em;">In-browser coding</div>
                    <div style="font-size:13px;color:#6e6580;line-height:1.5;margin-top:4px;">Live editor &amp; terminal. Nothing to install.</div>
                  </td>
                  <td valign="top" width="33%" style="padding-right:14px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
                    <div style="font-size:13px;font-weight:600;color:#1d1730;line-height:1.35;letter-spacing:-0.005em;">AI&#8209;assisted</div>
                    <div style="font-size:13px;color:#6e6580;line-height:1.5;margin-top:4px;">Use the built&#8209;in chat to debug &amp; reason out loud.</div>
                  </td>
                  <td valign="top" width="34%" class="last" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
                    <div style="font-size:13px;font-weight:600;color:#1d1730;line-height:1.35;letter-spacing:-0.005em;">~60 minutes</div>
                    <div style="font-size:13px;color:#6e6580;line-height:1.5;margin-top:4px;">One sitting, when it suits you. Save&nbsp;&amp;&nbsp;submit when done.</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Personal-link notice -->
          <tr>
            <td class="px-40" style="padding:24px 40px 8px 40px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f7f4fb;border:1px solid #efe8f7;border-radius:10px;">
                <tr>
                  <td style="padding:14px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.55;color:#2e2745;">
                    The link above is unique to you — please don&rsquo;t forward or share it. If you didn&rsquo;t apply to ${org_name}, you can ignore this email.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Sign-off -->
          <tr>
            <td class="px-40" style="padding:22px 40px 36px 40px;">
              <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;color:#2e2745;">
                Good luck,<br />
                The ${org_name} hiring team
              </p>
            </td>
          </tr>

          <!-- Footer: tiny Taali attribution -->
          <tr>
            <td class="px-32" style="padding:18px 32px 22px 32px;background-color:#fafaf8;border-top:1px solid #efe8f7;">
              <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;line-height:1.55;color:#9e96ae;">
                Sent on behalf of ${org_name} via <a href="https://taali.ai" target="_blank" style="color:#9e96ae;text-decoration:underline;">Taali</a>, their assessment platform. If you didn&rsquo;t apply, you can ignore this email — the link won&rsquo;t be reused.
              </p>
            </td>
          </tr>

        </table>
        <!-- /container -->

        <!-- Outer post-card spacer (some clients clip if absent) -->
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
          <tr><td style="height:24px;line-height:24px;font-size:24px;">&nbsp;</td></tr>
        </table>

      </td>
    </tr>
  </table>

</body>
</html>""")


def assessment_invite_html(
    candidate_name: str,
    org_name: str,
    position: str,
    assessment_link: str,
) -> str:
    """Candidate-facing assessment invite, branded as the org.

    ``org_name`` here is the *display* name the candidate should
    recognize — the resolved candidate-facing brand from the org's
    workspace settings, falling back to the legal entity name. The
    Taali platform attribution lives in the footer only, so the email
    reads as a continuation of the recruiter's prior comms (Workable
    application receipt, etc.).

    Renders the designer-handoff template at
    ``emails/assessment_invite.html`` (Taali Design System, 2026-05-07).
    All four variables are HTML-escaped before substitution.
    """
    return _ASSESSMENT_INVITE_HTML_TEMPLATE.substitute(
        candidate_name=_h(candidate_name),
        org_name=_h(org_name),
        position=_h(position),
        assessment_link=_h(assessment_link),
    )


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
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">{BRAND_NAME}</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">{BRAND_PRODUCT_NAME}</p>
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
                This notification was sent by {BRAND_NAME}. You are receiving this because
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


def candidate_feedback_ready_html(
    candidate_name: str,
    org_name: str,
    role_title: str,
    feedback_link: str,
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
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">{BRAND_NAME}</h1>
              <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">{BRAND_PRODUCT_NAME}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:40px;">
              <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Your AI collaboration results are ready</h2>
              <p style="margin:0 0 12px;color:#4b5563;font-size:16px;line-height:1.6;">
                Hi {candidate_name},
              </p>
              <p style="margin:0 0 20px;color:#4b5563;font-size:16px;line-height:1.6;">
                Your {BRAND_NAME} feedback report for <strong>{role_title}</strong> at <strong>{org_name}</strong>
                is now available.
              </p>
              <table cellpadding="0" cellspacing="0" style="margin:0 auto 22px;">
                <tr>
                  <td style="background-color:#6366f1;border-radius:6px;text-align:center;">
                    <a href="{feedback_link}"
                       style="display:inline-block;padding:14px 30px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">
                      View Feedback Report
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px;color:#9ca3af;font-size:13px;">Or copy this link into your browser:</p>
              <p style="margin:0;color:#6366f1;font-size:13px;word-break:break-all;">{feedback_link}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def assessment_expiry_reminder_html(
    candidate_name: str,
    task_name: str,
    assessment_link: str,
    expiry_text: str,
) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background-color:#d97706;padding:28px;text-align:center;">
          <h1 style="margin:0;color:#ffffff;font-size:26px;">{BRAND_NAME}</h1>
          <p style="margin:6px 0 0;color:#ffedd5;font-size:13px;">Assessment reminder</p>
        </td></tr>
        <tr><td style="padding:36px;">
          <h2 style="margin:0 0 14px;color:#1f2937;font-size:22px;">Your assessment expires soon</h2>
          <p style="margin:0 0 10px;color:#4b5563;font-size:16px;line-height:1.6;">Hi {candidate_name},</p>
          <p style="margin:0 0 10px;color:#4b5563;font-size:16px;line-height:1.6;">
            This is a reminder that your <strong>{task_name}</strong> assessment link expires on <strong>{expiry_text}</strong>.
          </p>
          <p style="margin:0 0 22px;color:#4b5563;font-size:16px;line-height:1.6;">
            Please complete your assessment before it expires.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin:0 auto 20px;"><tr><td style="background-color:#6366f1;border-radius:6px;text-align:center;">
            <a href="{assessment_link}" style="display:inline-block;padding:14px 28px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">Continue Assessment</a>
          </td></tr></table>
          <p style="margin:0;color:#9ca3af;font-size:13px;word-break:break-all;">{assessment_link}</p>
        </td></tr>
      </table>
    </td></tr>
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
          <h1 style="margin:0;color:#ffffff;font-size:28px;">{BRAND_NAME}</h1>
          <p style="margin:4px 0 0;color:#c7d2fe;font-size:14px;">{BRAND_PRODUCT_NAME}</p>
        </td></tr>
        <tr><td style="padding:40px;">
          <h2 style="margin:0 0 16px;color:#1f2937;font-size:22px;">Verify your email</h2>
          <p style="margin:0 0 8px;color:#4b5563;font-size:16px;line-height:1.6;">
            Hi {full_name},
          </p>
          <p style="margin:0 0 24px;color:#4b5563;font-size:16px;line-height:1.6;">
            Thanks for signing up for {BRAND_NAME}. Please verify your email address by clicking the button below.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;"><tr><td style="background-color:#6366f1;border-radius:6px;text-align:center;">
            <a href="{verification_link}" style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">Verify Email</a>
          </td></tr></table>
          <p style="margin:0 0 8px;color:#9ca3af;font-size:13px;">Or copy this link into your browser:</p>
          <p style="margin:0 0 24px;color:#6366f1;font-size:13px;word-break:break-all;">{verification_link}</p>
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
          <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;">
            This link expires in 24 hours. If you didn't create a {BRAND_NAME} account, you can ignore this email.
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
          <h1 style="margin:0;color:#ffffff;font-size:28px;">{BRAND_NAME}</h1>
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
