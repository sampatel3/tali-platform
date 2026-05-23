"""Tests for org-co-branded assessment invite emails.

Covers the 2026-05-07 follow-up that makes the assessment email recognizable
to candidates as a continuation of their Workable application thread:

- From display name uses ``candidate_facing_brand`` (or ``org_name``
  fallback) so inbox shows "Acme Hiring" not "TAALI"
- Subject line includes role + org
- ``reply_to`` header is set when the recruiter triggers the send so
  candidate replies route to the recruiter's inbox
- Resolution chain: candidate_facing_brand > org.name > platform brand
- Email body leads with the org brand, references the candidate's
  application, and keeps Taali attribution to a footer line only
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.components.notifications.email_client import (
    EmailService,
    _compose_from,
    _extract_address,
)


# ---------------------------------------------------------------------------
# Helpers — pure functions
# ---------------------------------------------------------------------------


def test_extract_address_handles_display_name_form():
    assert _extract_address("TAALI <noreply@taali.ai>") == "noreply@taali.ai"


def test_extract_address_handles_bare_address():
    assert _extract_address("noreply@taali.ai") == "noreply@taali.ai"


def test_extract_address_handles_empty_string():
    assert _extract_address("") == ""


def test_compose_from_uses_display_name_when_provided():
    out = _compose_from(base="TAALI <noreply@taali.ai>", display_name="Acme Hiring")
    assert out == '"Acme Hiring" <noreply@taali.ai>'


def test_compose_from_falls_back_to_base_when_no_display_name():
    out = _compose_from(base="TAALI <noreply@taali.ai>", display_name=None)
    assert out == "TAALI <noreply@taali.ai>"


def test_compose_from_strips_quotes_in_display_name():
    """Defensive: a display name with embedded quotes shouldn't break the from-line."""
    out = _compose_from(base="<noreply@taali.ai>", display_name='Bobby "Tables"')
    assert out == '"Bobby Tables" <noreply@taali.ai>'


def test_compose_from_falls_back_when_base_has_no_address():
    out = _compose_from(base="garbage_no_at_sign", display_name="Acme")
    assert out == "garbage_no_at_sign"


# ---------------------------------------------------------------------------
# EmailService — inspected through the Resend payload
# ---------------------------------------------------------------------------


def _send_with_resend_capture(**kwargs) -> dict:
    """Helper: call EmailService.send_assessment_invite with Resend mocked,
    return the dict that was passed to ``resend.Emails.send``."""
    captured: dict = {}

    def _capture(payload):
        captured.update(payload)
        return {"id": "fake-id"}

    svc = EmailService(api_key="rk_test", from_email="TAALI <noreply@taali.ai>")
    with patch(
        "app.components.notifications.email_client.resend.Emails.send",
        side_effect=_capture,
    ):
        result = svc.send_assessment_invite(**kwargs)
    assert result["success"] is True
    return captured


def _base_kwargs(**overrides) -> dict:
    base = {
        "candidate_email": "alice@x.test",
        "candidate_name": "Alice",
        "token": "tk-abc",
        "assessment_id": 42,
        "org_name": "Acme Hiring Inc",
        "position": "Senior Backend Engineer",
        "frontend_url": "https://app.taali.test",
    }
    base.update(overrides)
    return base


def test_from_display_name_uses_candidate_facing_brand_when_set():
    payload = _send_with_resend_capture(
        **_base_kwargs(candidate_facing_brand="Acme Careers")
    )
    assert payload["from"] == '"Acme Careers" <noreply@taali.ai>'


def test_from_display_name_falls_back_to_org_name_when_brand_blank():
    payload = _send_with_resend_capture(**_base_kwargs(candidate_facing_brand=None))
    assert payload["from"] == '"Acme Hiring Inc" <noreply@taali.ai>'


def test_from_display_name_falls_back_to_platform_brand_when_org_name_blank():
    """Defensive: empty org_name + no brand → fall back to 'TAALI' so the
    email still has a sensible from-line."""
    payload = _send_with_resend_capture(
        **_base_kwargs(org_name="", candidate_facing_brand=None)
    )
    # Should NOT be empty string + angle-brackets; should fall back.
    assert payload["from"].startswith('"')
    assert "<noreply@taali.ai>" in payload["from"]


def test_subject_includes_role_and_resolved_brand():
    payload = _send_with_resend_capture(
        **_base_kwargs(candidate_facing_brand="Acme Careers")
    )
    assert payload["subject"] == "Your Senior Backend Engineer assessment at Acme Careers"


def test_subject_uses_org_name_when_brand_not_set():
    payload = _send_with_resend_capture(**_base_kwargs(candidate_facing_brand=None))
    assert "Acme Hiring Inc" in payload["subject"]


def test_reply_to_passed_through_when_set():
    payload = _send_with_resend_capture(
        **_base_kwargs(reply_to="recruiter@acmehiring.com")
    )
    assert payload["reply_to"] == "recruiter@acmehiring.com"


def test_reply_to_omitted_when_none():
    payload = _send_with_resend_capture(**_base_kwargs(reply_to=None))
    assert "reply_to" not in payload


def test_reply_to_omitted_when_blank():
    payload = _send_with_resend_capture(**_base_kwargs(reply_to="  "))
    assert "reply_to" not in payload


def test_html_body_uses_resolved_brand_in_header_and_body():
    """The visible header should be the org brand, not 'TAALI', and the
    body should reference the candidate's application context.

    Asserts only on structural invariants (org name, role, candidate
    name, assessment link present; platform name only as a small
    footer attribution). Copy itself is intentionally NOT asserted so
    the design team can iterate on the wording without breaking CI.
    """
    payload = _send_with_resend_capture(
        **_base_kwargs(candidate_facing_brand="Acme Careers")
    )
    html = payload["html"]
    assert "Acme Careers" in html
    # Platform brand still appears in the footer attribution but should
    # not dominate. Two occurrences max (allows BRAND_NAME in alt-text
    # or accessibility additions designers may add).
    assert html.count("TAALI") <= 2
    assert "Senior Backend Engineer" in html
    assert "Alice" in html
    assert "https://app.taali.test/assessment/42" in html


def test_plain_text_body_is_sent_alongside_html():
    """Resend payload must include a ``text`` field for accessibility +
    inbox preview. Same content shape as HTML; no markup."""
    payload = _send_with_resend_capture(
        **_base_kwargs(candidate_facing_brand="Acme Careers")
    )
    assert "text" in payload
    text = payload["text"]
    # Same identifying details candidates need.
    assert "Acme Careers" in text
    assert "Senior Backend Engineer" in text
    assert "Alice" in text
    assert "https://app.taali.test/assessment/42" in text
    # Plain text — should not contain HTML tags.
    assert "<" not in text and ">" not in text


def test_html_escapes_org_name_with_special_characters():
    """Org name like 'Acme & Co' or '<X>' must not corrupt rendered HTML."""
    payload = _send_with_resend_capture(
        **_base_kwargs(
            org_name="Bobby <Tables> & Co",
            candidate_facing_brand=None,
        )
    )
    html = payload["html"]
    # Raw angle brackets and ampersand should NOT appear unescaped in the body.
    assert "Bobby <Tables>" not in html
    assert "Bobby &lt;Tables&gt; &amp; Co" in html


def test_html_escapes_candidate_name_with_special_characters():
    payload = _send_with_resend_capture(
        **_base_kwargs(
            candidate_name="O'Brien <hacker>",
            candidate_facing_brand="Acme",
        )
    )
    html = payload["html"]
    assert "<hacker>" not in html
    assert "O&#x27;Brien &lt;hacker&gt;" in html


# ---------------------------------------------------------------------------
# Integration through dispatch_assessment_invite
# ---------------------------------------------------------------------------


def test_dispatch_resolves_candidate_facing_brand_from_workspace_settings(db, monkeypatch):
    """End-to-end: dispatch_assessment_invite should pull
    candidate_facing_brand from org.workspace_settings JSON and pass it
    down to the email send."""
    from app.domains.integrations_notifications.invite_flow import dispatch_assessment_invite
    from app.models.assessment import Assessment
    from app.models.candidate import Candidate
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.task import Task
    from app.platform.config import settings as cfg
    from datetime import datetime, timezone

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = Organization(
        name="Acme Hiring Inc",
        slug=f"acme-{id(db)}",
        workable_connected=False,
        workspace_settings={"candidate_facing_brand": "Acme Careers"},
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    task = Task(
        name="Coding Task",
        task_key=f"task-{id(db)}",
        organization_id=org.id,
        is_active=True,
    )
    db.add(task)
    db.flush()
    candidate = Candidate(
        organization_id=org.id, email="alice@x.test", full_name="Alice"
    )
    db.add(candidate)
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        role_id=role.id,
        token="tok-abc",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
        candidate_feedback_enabled=True,
    )
    db.add(a)
    db.flush()

    with patch(
        "app.components.notifications.tasks.send_assessment_email"
    ) as mock_celery:
        dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Senior Backend Engineer",
            reply_to="recruiter@acmehiring.com",
        )

    assert mock_celery.delay.called
    kwargs = mock_celery.delay.call_args.kwargs
    assert kwargs["candidate_facing_brand"] == "Acme Careers"
    assert kwargs["reply_to"] == "recruiter@acmehiring.com"
    assert kwargs["org_name"] == "Acme Hiring Inc"


def test_dispatch_passes_none_brand_when_workspace_setting_missing(db, monkeypatch):
    """Org with no workspace_settings → candidate_facing_brand=None,
    EmailService falls back to org.name."""
    from app.domains.integrations_notifications.invite_flow import dispatch_assessment_invite
    from app.models.assessment import Assessment
    from app.models.candidate import Candidate
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.task import Task
    from app.platform.config import settings as cfg
    from datetime import datetime, timezone

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = Organization(
        name="Acme Hiring Inc", slug=f"a2-{id(db)}", workspace_settings=None,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    task = Task(
        name="Coding Task", task_key=f"t-{id(db)}",
        organization_id=org.id, is_active=True,
    )
    db.add(task)
    db.flush()
    candidate = Candidate(
        organization_id=org.id, email="bob@x.test", full_name="Bob"
    )
    db.add(candidate)
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        role_id=role.id,
        token="tok-bob",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
        candidate_feedback_enabled=True,
    )
    db.add(a)
    db.flush()

    with patch(
        "app.components.notifications.tasks.send_assessment_email"
    ) as mock_celery:
        dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="bob@x.test",
            candidate_name="Bob",
            position="Backend",
        )

    kwargs = mock_celery.delay.call_args.kwargs
    assert kwargs["candidate_facing_brand"] is None
    assert kwargs["reply_to"] is None  # default when caller doesn't set it
