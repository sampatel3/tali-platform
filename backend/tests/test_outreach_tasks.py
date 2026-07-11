"""Outreach Celery tasks + webhook correlation + interest capture.

Covers:
- generate task: metered draft written, Feature.OUTREACH_DRAFT metering asserted,
  failure isolation (one bad message → failed, campaign still ready).
- send task: suppression re-check skip, unsubscribe footer + reply_to +
  List-Unsubscribe header present, resend_email_id stored, only-approved sends,
  per-message failure isolation.
- webhook: outreach event correlation + ratchet (never downgrade).
- interest endpoint: ratchet + redirect with/without job page + 404.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.organization import Organization
from app.models.outreach_campaign import (
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INTERESTED,
    MESSAGE_STATUS_PENDING,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SUPPRESSED,
    OutreachCampaign,
    OutreachMessage,
)
from app.models.prospect import Prospect
from app.models.user import User
from app.services.email_suppression_service import suppress
from app.services.resend_webhook_service import apply_resend_event
from tests.conftest import auth_headers


def _org_and_user(db):
    org = Organization(name="Acme", slug=f"org-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"rec-{id(db)}@example.com",
        hashed_password="x",
        is_active=True,
        organization_id=org.id,
        full_name="Rec Ruiter",
    )
    db.add(user)
    db.commit()
    return org, user


def _campaign(db, org_id, user_id=None, job_page_token=None, status="ready"):
    c = OutreachCampaign(
        organization_id=org_id,
        name="Wave",
        brief="Reaching out about the Backend role.",
        status=status,
        created_by_user_id=user_id,
        job_page_token=job_page_token,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _msg(db, campaign, org_id, email, status=MESSAGE_STATUS_PENDING, **kw):
    m = OutreachMessage(
        campaign_id=campaign.id,
        organization_id=org_id,
        email=email,
        recipient_name=kw.pop("name", "Recipient"),
        status=status,
        **kw,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# ---------------------------------------------------------------------------
# Generate task
# ---------------------------------------------------------------------------


def test_generate_writes_draft_and_meters(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id, status="generating")
    m = _msg(db, c, org.id, "gen@example.com", status=MESSAGE_STATUS_PENDING)

    from app.tasks import outreach_tasks

    fake_client = MagicMock()

    class _R:
        ok = True
        value = type("V", (), {"subject": "Hello", "body": "Hi there {{cta_url}}"})()
        error_reason = None

    with patch("app.services.claude_client_resolver.get_metered_client", return_value=fake_client), \
         patch("app.llm.structured.generate_structured", return_value=_R()) as gen:
        outreach_tasks.generate_campaign_drafts(c.id)

    db.refresh(m)
    db.refresh(c)
    assert m.status == MESSAGE_STATUS_DRAFT
    assert m.body and "{{cta_url}}" in m.body
    assert c.status == "ready"
    # Metering: the Feature.OUTREACH_DRAFT context + entity id are passed.
    _, kwargs = gen.call_args
    meter = kwargs["metering"]
    assert str(getattr(meter.feature, "value", meter.feature)) == "outreach_draft"
    assert meter.entity_id == f"outreach_msg:{m.id}"


def test_generate_failure_isolated(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id, status="generating")
    ok_msg = _msg(db, c, org.id, "ok@example.com", status=MESSAGE_STATUS_PENDING)
    bad_msg = _msg(db, c, org.id, "bad@example.com", status=MESSAGE_STATUS_PENDING)

    from app.tasks import outreach_tasks

    class _OK:
        ok = True
        value = type("V", (), {"subject": "S", "body": "B {{cta_url}}"})()
        error_reason = None

    def _side_effect(*a, **k):
        if k["metering"].entity_id == f"outreach_msg:{bad_msg.id}":
            raise RuntimeError("boom")
        return _OK()

    with patch("app.services.claude_client_resolver.get_metered_client", return_value=MagicMock()), \
         patch("app.llm.structured.generate_structured", side_effect=_side_effect):
        outreach_tasks.generate_campaign_drafts(c.id)

    db.refresh(ok_msg)
    db.refresh(bad_msg)
    db.refresh(c)
    assert ok_msg.status == MESSAGE_STATUS_DRAFT
    assert bad_msg.status == MESSAGE_STATUS_FAILED
    assert c.status == "ready"  # campaign still reaches ready


# ---------------------------------------------------------------------------
# Send task
# ---------------------------------------------------------------------------


def _run_send(db, campaign_id, send_result=None):
    from app.tasks import outreach_tasks

    fake_email = MagicMock()
    fake_email.send_outreach_email.return_value = send_result or {
        "success": True,
        "email_id": "re_out_1",
    }
    with patch(
        "app.components.notifications.email_client.EmailService", return_value=fake_email
    ), patch.object(outreach_tasks.time, "sleep", return_value=None):
        outreach_tasks.send_campaign_messages(campaign_id)
    return fake_email


def test_send_only_approved_with_footer_and_headers(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id)
    approved = _msg(
        db, c, org.id, "yes@example.com",
        status=MESSAGE_STATUS_APPROVED, subject="Hi", body="Body {{cta_url}}",
    )
    # A draft (not approved) must NEVER be sent.
    _msg(db, c, org.id, "no@example.com", status=MESSAGE_STATUS_DRAFT, body="x {{cta_url}}")

    fake_email = _run_send(db, c.id)

    # Exactly one send — the approved message.
    assert fake_email.send_outreach_email.call_count == 1
    _, kwargs = fake_email.send_outreach_email.call_args
    assert kwargs["to_email"] == "yes@example.com"
    # reply_to = the creating recruiter's email.
    assert kwargs["reply_to"] == user.email
    # unsubscribe footer present in the text body + an unsubscribe_url passed
    # (the email client turns it into the List-Unsubscribe header).
    assert "Unsubscribe" in kwargs["text_body"]
    assert "/unsubscribe/" in kwargs["unsubscribe_url"]
    # CTA placeholder replaced with the interest link.
    assert "{{cta_url}}" not in kwargs["text_body"]
    assert "/api/v1/public/outreach/interest/" in kwargs["text_body"]

    db.refresh(approved)
    assert approved.status == MESSAGE_STATUS_SENT
    assert approved.resend_email_id == "re_out_1"


def test_send_rechecks_suppression(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id)
    m = _msg(
        db, c, org.id, "blocked@example.com",
        status=MESSAGE_STATUS_APPROVED, body="B {{cta_url}}",
    )
    # Suppression lands AFTER approval — send must skip it.
    suppress(db, email="blocked@example.com", reason="unsubscribed", organization_id=org.id)

    fake_email = _run_send(db, c.id)
    assert fake_email.send_outreach_email.call_count == 0
    db.refresh(m)
    assert m.status == MESSAGE_STATUS_SUPPRESSED


def test_send_failure_isolated(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id)
    m = _msg(
        db, c, org.id, "fail@example.com",
        status=MESSAGE_STATUS_APPROVED, body="B {{cta_url}}",
    )
    fake_email = _run_send(
        db, c.id, send_result={"success": False, "error": "resend down"}
    )
    assert fake_email.send_outreach_email.call_count == 1
    db.refresh(m)
    assert m.status == MESSAGE_STATUS_FAILED
    assert "resend down" in (m.error or "")


def test_email_client_sets_list_unsubscribe_header_and_reply_to():
    """The EmailService.send_outreach_email wire layer: List-Unsubscribe header
    (URL form) + List-Unsubscribe-Post + reply_to are present on the Resend
    payload. Mock only the low-level send so we assert the real header build."""
    from app.components.notifications import email_client

    captured = {}

    def _fake_send(payload, *, recipient):
        captured["payload"] = payload
        return {"id": "re_hdr_1"}

    with patch.object(email_client, "_send_resend_email", side_effect=_fake_send):
        svc = email_client.EmailService(api_key="k", from_email="TAALI <noreply@taali.ai>")
        res = svc.send_outreach_email(
            to_email="x@example.com",
            subject="Subj",
            text_body="Body\n\n---\nNot interested? Unsubscribe: https://app/unsubscribe/tok",
            html_body="<div>Body</div>",
            reply_to="recruiter@example.com",
            unsubscribe_url="https://app/unsubscribe/tok",
            display_name="Acme",
        )
    assert res["success"] is True
    payload = captured["payload"]
    assert payload["reply_to"] == "recruiter@example.com"
    headers = payload["headers"]
    assert headers["List-Unsubscribe"] == "<https://app/unsubscribe/tok>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ---------------------------------------------------------------------------
# Webhook correlation
# ---------------------------------------------------------------------------


def test_webhook_correlates_outreach_and_ratchets(db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id)
    m = _msg(
        db, c, org.id, "track@example.com",
        status=MESSAGE_STATUS_SENT, resend_email_id="re_track",
    )
    res = apply_resend_event(db, {"type": "email.opened", "data": {"email_id": "re_track"}})
    assert res["status"] == "applied"
    assert res["outreach_message_id"] == m.id
    db.refresh(m)
    assert m.status == "opened"
    assert m.opened_at is not None

    # A late 'delivered' must not downgrade an opened message.
    apply_resend_event(db, {"type": "email.delivered", "data": {"email_id": "re_track"}})
    db.refresh(m)
    assert m.status == "opened"


def test_webhook_unknown_id_ignored(db):
    res = apply_resend_event(db, {"type": "email.opened", "data": {"email_id": "nope"}})
    assert res["status"] == "ignored"


# ---------------------------------------------------------------------------
# Interest capture endpoint
# ---------------------------------------------------------------------------


def test_interest_ratchets_and_redirects_thanks(client, db):
    # Build a campaign + message directly, no job page → redirect to thanks.
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id, job_page_token=None)
    prospect = Prospect(organization_id=org.id, full_name="P", email="p@example.com")
    db.add(prospect)
    db.flush()
    m = _msg(db, c, org.id, "p@example.com", status=MESSAGE_STATUS_SENT, prospect_id=prospect.id)
    token = m.interest_token

    resp = client.get(
        f"/api/v1/public/outreach/interest/{token}", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/outreach/thanks" in resp.headers["location"]

    db.refresh(m)
    db.refresh(prospect)
    assert m.status == MESSAGE_STATUS_INTERESTED
    assert m.interested_at is not None
    assert prospect.status == "interested"


def test_interest_redirects_to_job_page(client, db):
    org, user = _org_and_user(db)
    c = _campaign(db, org.id, user.id, job_page_token="jobtok123")
    m = _msg(db, c, org.id, "j@example.com", status=MESSAGE_STATUS_SENT)
    resp = client.get(
        f"/api/v1/public/outreach/interest/{m.interest_token}", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/job/jobtok123" in resp.headers["location"]


def test_interest_invalid_token_404(client):
    resp = client.get(
        "/api/v1/public/outreach/interest/not-a-real-token", follow_redirects=False
    )
    assert resp.status_code == 404
