"""Public demo-lead capture: POST /api/v1/public/demo-lead (no auth).

The marketing "book a demo" form posts here; the lead is forwarded to
hello@ by email (BackgroundTasks → EmailService.send_internal_alert).
TestClient runs background tasks synchronously, so the forward is
asserted through the mocked EmailService.
"""
from unittest.mock import patch

import pytest

from app.domains.marketing_leads import routes as lead_routes


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    lead_routes.reset()
    yield
    lead_routes.reset()


def _post(client, body=None, ip="203.0.113.7", forwarded_for=None):
    payload = {"email": "jane@acme-corp.io", "name": "Jane", "company": "Acme",
               "role": "Backend", "volume": "6–20"}
    if body is not None:
        payload = body
    return client.post(
        "/api/v1/public/demo-lead",
        json=payload,
        headers={
            "x-real-ip": ip,
            "x-forwarded-for": forwarded_for or ip,
        },
    )


def test_lead_is_forwarded_to_hello_inbox(client, monkeypatch):
    monkeypatch.setattr(lead_routes.settings, "RESEND_API_KEY", "rk_test")
    sent = {}

    def _capture(self, *, to_email, subject, text_body):
        sent.update({"to": to_email, "subject": subject, "body": text_body})
        return {"success": True, "email_id": "fake"}

    with patch(
        "app.components.notifications.email_client.EmailService.send_internal_alert",
        _capture,
    ):
        resp = _post(client)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert sent["to"] == "hello@taali.ai"
    assert "jane@acme-corp.io" in sent["subject"]
    assert "Acme" in sent["subject"]
    assert "Jane" in sent["body"]
    assert "6–20" in sent["body"]


def test_missing_resend_key_still_returns_ok(client, monkeypatch):
    """Local/dev environments without Resend must not error the form."""
    monkeypatch.setattr(lead_routes.settings, "RESEND_API_KEY", "")
    resp = _post(client)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_invalid_email_rejected(client):
    resp = _post(client, body={"email": "not-an-email"})
    assert resp.status_code == 422


def test_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(lead_routes.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(lead_routes.settings, "TRUST_RAILWAY_X_REAL_IP", True)
    for _ in range(lead_routes._MAX_PER_WINDOW):
        assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    # A different client IP is not affected.
    assert _post(client, ip="198.51.100.9").status_code == 200


def test_spoofed_forwarded_prefix_cannot_mint_fresh_buckets(client, monkeypatch):
    """Railway's canonical real IP wins over attacker-controlled XFF."""
    monkeypatch.setattr(lead_routes.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(lead_routes.settings, "TRUST_RAILWAY_X_REAL_IP", True)
    for i in range(lead_routes._MAX_PER_WINDOW):
        assert _post(
            client,
            ip="203.0.113.99",
            forwarded_for=f"10.0.0.{i}",
        ).status_code == 200
    assert _post(
        client,
        ip="203.0.113.99",
        forwarded_for="10.9.9.9",
    ).status_code == 429


def test_untrusted_forwarded_for_cannot_split_marketing_buckets(client, monkeypatch):
    monkeypatch.setattr(lead_routes.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(lead_routes.settings, "TRUST_RAILWAY_X_REAL_IP", False)
    monkeypatch.setattr(lead_routes.settings, "TRUSTED_PROXY_CIDRS", "")
    for i in range(lead_routes._MAX_PER_WINDOW):
        assert _post(client, forwarded_for=f"10.0.0.{i}").status_code == 200
    assert _post(client, forwarded_for="10.9.9.9").status_code == 429
