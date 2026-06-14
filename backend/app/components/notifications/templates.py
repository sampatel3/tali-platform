"""HTML email templates for platform notifications."""

from html import escape as _html_escape
from string import Template as _StringTemplate

from ...platform.brand import BRAND_NAME


def _h(value: str | None) -> str:
    """HTML-escape a value for safe interpolation in templates.

    Org names like ``"Acme & Co"`` or candidate names containing ``<``
    would otherwise corrupt the rendered email. ``None`` is rendered as
    an empty string so optional fields don't print "None".
    """
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


# =============================================================================
# Taali Email Design System — shared shell + body helpers
# =============================================================================
# Used by every transactional email except ``assessment_invite_html``, which
# has its own self-contained template (designer handoff 2026-05-07). Future
# cleanup can de-dup that one too. Tokens here mirror the canonical template
# so all emails read as one coherent system: cream background, dark plum
# text, purple accents, pill CTA.

_TAALI_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _taali_paragraph(html: str) -> str:
    return (
        f'<p class="body-lg" style="margin:0 0 16px 0;font-family:{_TAALI_FONT};'
        f'font-size:16px;line-height:1.6;color:#2e2745;">{html}</p>'
    )


def _taali_intro(html_paragraphs: str) -> str:
    return (
        f'<tr><td class="px-40" style="padding:8px 40px 4px 40px;">'
        f'{html_paragraphs}'
        f'</td></tr>'
    )


def _taali_cta_row(label: str, link: str) -> str:
    return (
        f'<tr><td class="px-40" style="padding:18px 40px 8px 40px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" class="btn-cta">'
        f'<tr><td bgcolor="#1d1730" style="border-radius:999px;mso-padding-alt:14px 28px;">'
        f'<!--[if mso]>&nbsp;<![endif]-->'
        f'<a href="{link}" target="_blank" style="display:inline-block;padding:14px 28px;'
        f'font-family:{_TAALI_FONT};font-size:15px;font-weight:600;line-height:1;'
        f'color:#ffffff;background-color:#1d1730;border-radius:999px;letter-spacing:-0.005em;">'
        f'{label}&nbsp;&rarr;</a>'
        f'<!--[if mso]>&nbsp;<![endif]-->'
        f'</td></tr></table></td></tr>'
    )


def _taali_link_fallback(link: str) -> str:
    return (
        f'<tr><td class="px-40" style="padding:14px 40px 8px 40px;">'
        f'<p style="margin:0;font-family:{_TAALI_FONT};font-size:13px;'
        f'line-height:1.55;color:#6e6580;">'
        f'Button not working? Paste this into your browser:<br />'
        f'<a href="{link}" target="_blank" style="color:#6b21e8;text-decoration:underline;'
        f'word-break:break-all;">{link}</a>'
        f'</p></td></tr>'
    )


def _taali_notice_card(inner_html: str) -> str:
    return (
        f'<tr><td class="px-40" style="padding:24px 40px 8px 40px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:#f7f4fb;border:1px solid #efe8f7;border-radius:10px;">'
        f'<tr><td style="padding:14px 16px;font-family:{_TAALI_FONT};font-size:13px;'
        f'line-height:1.55;color:#2e2745;">{inner_html}</td></tr></table>'
        f'</td></tr>'
    )


def _taali_signoff(inner_html: str) -> str:
    return (
        f'<tr><td class="px-40" style="padding:22px 40px 36px 40px;">'
        f'<p style="margin:0;font-family:{_TAALI_FONT};font-size:14px;line-height:1.55;'
        f'color:#2e2745;">{inner_html}</p>'
        f'</td></tr>'
    )


def _taali_score_callout(score: float) -> str:
    """Big composite score in a Taali notice-card. Always plum, no
    red/amber/green — at-a-glance is the headline number, not the colour
    (matches the in-product convention of using purple variations rather
    than traffic-light colours)."""
    return (
        f'<tr><td class="px-40" style="padding:18px 40px 8px 40px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:#f7f4fb;border:1px solid #efe8f7;border-radius:10px;">'
        f'<tr><td style="padding:22px 24px;text-align:center;font-family:{_TAALI_FONT};">'
        f'<div style="font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#6e6580;">Composite score</div>'
        f'<div style="margin-top:8px;font-size:42px;font-weight:600;letter-spacing:-0.02em;color:#1d1730;line-height:1;">{score:.0f}<span style="font-size:24px;color:#6e6580;">%</span></div>'
        f'</td></tr></table>'
        f'</td></tr>'
    )


_TAALI_EMAIL_SHELL = _StringTemplate("""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="color-scheme" content="light only" />
  <meta name="supported-color-schemes" content="light only" />
  <title>${title}</title>
  <!--[if mso]>
  <style type="text/css">
    table, td, div, h1, h2, p { font-family: Arial, Helvetica, sans-serif !important; }
    .btn-cta a { padding: 14px 28px !important; }
  </style>
  <![endif]-->
  <style type="text/css">
    body { margin: 0 !important; padding: 0 !important; width: 100% !important; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
    table { border-collapse: collapse !important; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
    img { border: 0; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }
    a { text-decoration: none; }
    @media screen and (max-width: 620px) {
      .container { width: 100% !important; }
      .px-32 { padding-left: 24px !important; padding-right: 24px !important; }
      .px-40 { padding-left: 24px !important; padding-right: 24px !important; }
      .h-display { font-size: 22px !important; line-height: 1.25 !important; }
      .body-lg { font-size: 16px !important; line-height: 1.55 !important; }
      .btn-cta a { display: block !important; width: 100% !important; box-sizing: border-box !important; text-align: center !important; }
    }
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f4f3f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1730;">

  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f4f3f0;opacity:0;">
    ${preview}
    &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp; &#847; &zwnj; &nbsp;
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f3f0;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background-color:#ffffff;border:1px solid #e7e0f0;border-radius:14px;overflow:hidden;">

          <tr>
            <td class="px-32" style="padding:22px 32px;border-bottom:1px solid #efe8f7;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="left" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#6e6580;">${eyebrow_left}</td>
                  <td align="right" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#9e96ae;">${eyebrow_right}</td>
                </tr>
              </table>
            </td>
          </tr>

          ${header_block}

          ${body}

          <tr>
            <td class="px-32" style="padding:18px 32px 22px 32px;background-color:#fafaf8;border-top:1px solid #efe8f7;">
              <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;line-height:1.55;color:#9e96ae;">
                ${footer}
              </p>
            </td>
          </tr>

        </table>
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
          <tr><td style="height:24px;line-height:24px;font-size:24px;">&nbsp;</td></tr>
        </table>
      </td>
    </tr>
  </table>

</body>
</html>""")


def _taali_header_block(*, subtitle: str, headline: str) -> str:
    """Render subtitle (optional) + headline as one HTML block.

    Adjusts headline top padding so vertical rhythm is correct whether
    or not a subtitle line is present.
    """
    headline_html = (
        f'<h1 class="h-display" style="margin:0;font-family:{_TAALI_FONT};'
        f'font-size:26px;line-height:1.22;font-weight:600;'
        f'letter-spacing:-0.02em;color:#1d1730;">{headline}</h1>'
    )
    if subtitle:
        return (
            f'<tr><td class="px-40" style="padding:36px 40px 8px 40px;">'
            f'<div style="font-family:{_TAALI_FONT};font-size:13px;font-weight:500;'
            f'color:#6e6580;letter-spacing:-0.005em;">{subtitle}</div>'
            f'</td></tr>'
            f'<tr><td class="px-40" style="padding:6px 40px 18px 40px;">'
            f'{headline_html}</td></tr>'
        )
    return (
        f'<tr><td class="px-40" style="padding:36px 40px 18px 40px;">'
        f'{headline_html}</td></tr>'
    )


def _render_taali_email(
    *,
    title: str,
    preview: str,
    eyebrow_left: str,
    eyebrow_right: str,
    subtitle: str,
    headline: str,
    body: str,
    footer: str,
) -> str:
    return _TAALI_EMAIL_SHELL.substitute(
        title=title,
        preview=preview,
        eyebrow_left=eyebrow_left,
        eyebrow_right=eyebrow_right,
        header_block=_taali_header_block(subtitle=subtitle, headline=headline),
        body=body,
        footer=footer,
    )


def _taali_footer_org(org_name_safe: str) -> str:
    return (
        f'Sent on behalf of {org_name_safe} via '
        f'<a href="https://taali.ai" target="_blank" style="color:#9e96ae;text-decoration:underline;">Taali</a>, '
        f'their assessment platform.'
    )


def _taali_footer_brand(extra: str = "") -> str:
    base = (
        f'Sent by <a href="https://taali.ai" target="_blank" '
        f'style="color:#9e96ae;text-decoration:underline;">Taali</a>.'
    )
    return f"{base} {extra}".strip()


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
    """Recruiter-facing assessment-completed notification.

    Score callout is a single plum tone (no traffic-light colours) to
    match the in-product convention of using purple variations rather
    than red/amber/green.
    """
    cand = _h(candidate_name) or "A candidate"
    link = _h(results_link)
    intro = _taali_intro(
        _taali_paragraph(
            f'<strong style="color:#1d1730;font-weight:600;">{cand}</strong> has '
            f'completed their technical assessment. The full breakdown — radar, '
            f'prompt log, fit analysis — is ready in the dashboard.'
        )
    )
    body = (
        intro
        + _taali_score_callout(score)
        + _taali_cta_row("View full results", link)
        + _taali_link_fallback(link)
    )
    return _render_taali_email(
        title=f"Results: {cand} — {score:.0f}%",
        preview=f"{cand} scored {score:.0f}%. View the full breakdown in {BRAND_NAME}.",
        eyebrow_left="Assessment",
        eyebrow_right="Results ready",
        subtitle=BRAND_NAME,
        headline=f"{cand}&rsquo;s assessment is in",
        body=body,
        footer=_taali_footer_brand(
            "You&rsquo;re receiving this because you&rsquo;re listed as a reviewer."
        ),
    )


def candidate_feedback_ready_html(
    candidate_name: str,
    org_name: str,
    role_title: str,
    feedback_link: str,
) -> str:
    cand = _h(candidate_name) or "there"
    org = _h(org_name)
    role = _h(role_title)
    link = _h(feedback_link)
    intro = _taali_intro(
        _taali_paragraph(f"Hi {cand},")
        + _taali_paragraph(
            f'Your AI-collaboration feedback report for the '
            f'<strong style="color:#1d1730;font-weight:600;">{role}</strong> role at '
            f'<strong style="color:#1d1730;font-weight:600;">{org}</strong> is now available.'
        )
    )
    body = (
        intro
        + _taali_cta_row("View feedback report", link)
        + _taali_link_fallback(link)
    )
    return _render_taali_email(
        title=f"Your feedback report — {org}",
        preview=f"Your AI-collaboration feedback for {role} at {org} is ready.",
        eyebrow_left="Application",
        eyebrow_right="Feedback ready",
        subtitle=org,
        headline="Your feedback report is ready",
        body=body,
        footer=_taali_footer_org(org),
    )


def assessment_expiry_reminder_html(
    candidate_name: str,
    task_name: str,
    assessment_link: str,
    expiry_text: str,
) -> str:
    cand = _h(candidate_name) or "there"
    task = _h(task_name)
    link = _h(assessment_link)
    expiry = _h(expiry_text)
    intro = _taali_intro(
        _taali_paragraph(f"Hi {cand},")
        + _taali_paragraph(
            f'This is a reminder that your '
            f'<strong style="color:#1d1730;font-weight:600;">{task}</strong> '
            f'assessment link expires on '
            f'<strong style="color:#1d1730;font-weight:600;">{expiry}</strong>. '
            f'Please complete your assessment before then.'
        )
    )
    body = (
        intro
        + _taali_cta_row("Continue assessment", link)
        + _taali_link_fallback(link)
    )
    return _render_taali_email(
        title=f"Your {BRAND_NAME} assessment expires soon",
        preview=f"Your assessment expires on {expiry}. Continue when you&rsquo;re ready.",
        eyebrow_left="Assessment",
        eyebrow_right="Reminder",
        subtitle=BRAND_NAME,
        headline="Your assessment expires soon",
        body=body,
        footer=_taali_footer_brand(
            "If you&rsquo;ve already submitted, you can ignore this email."
        ),
    )


def email_verification_html(full_name: str, verification_link: str) -> str:
    name = _h(full_name) or "there"
    link = _h(verification_link)
    intro = _taali_intro(
        _taali_paragraph(f"Hi {name},")
        + _taali_paragraph(
            f'Thanks for signing up for '
            f'<strong style="color:#1d1730;font-weight:600;">{BRAND_NAME}</strong>. '
            f'Please verify your email address so we can finish setting up your workspace.'
        )
    )
    body = (
        intro
        + _taali_cta_row("Verify email", link)
        + _taali_link_fallback(link)
        + _taali_notice_card(
            f"This link expires in 24 hours. If you didn&rsquo;t create a "
            f"{BRAND_NAME} account, you can ignore this email."
        )
    )
    return _render_taali_email(
        title=f"Verify your email — {BRAND_NAME}",
        preview=f"Confirm your {BRAND_NAME} email address. Link expires in 24 hours.",
        eyebrow_left="Account",
        eyebrow_right="Verify email",
        subtitle=BRAND_NAME,
        headline="Verify your email",
        body=body,
        footer=_taali_footer_brand("If this wasn&rsquo;t you, no action is needed."),
    )


def password_reset_html(reset_link: str) -> str:
    link = _h(reset_link)
    intro = _taali_intro(
        _taali_paragraph(
            f'Click the button below to set a new password for your '
            f'<strong style="color:#1d1730;font-weight:600;">{BRAND_NAME}</strong> '
            f'account. This link expires in 1 hour.'
        )
    )
    body = (
        intro
        + _taali_cta_row("Reset password", link)
        + _taali_link_fallback(link)
        + _taali_notice_card(
            "If you didn&rsquo;t request this, you can ignore this email — "
            "your password won&rsquo;t change."
        )
    )
    return _render_taali_email(
        title=f"Reset your password — {BRAND_NAME}",
        preview=f"Reset your {BRAND_NAME} password. Link expires in 1 hour.",
        eyebrow_left="Account",
        eyebrow_right="Reset password",
        subtitle=BRAND_NAME,
        headline="Reset your password",
        body=body,
        footer=_taali_footer_brand("If this wasn&rsquo;t you, no action is needed."),
    )
