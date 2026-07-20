"""Regression coverage for bounded, exact-byte signed webhook requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.domains.billing_webhooks import webhook_routes
from app.domains.billing_webhooks.request_body import (
    MAX_SIGNED_WEBHOOK_BODY_BYTES,
    read_signed_webhook_body,
)
from app.models.organization import Organization


def _padded_json(size: int) -> bytes:
    prefix = b'{"type":"exact-limit","padding":"'
    suffix = b'"}'
    body = prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix
    assert len(body) == size
    return body


def _svix_signature(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = b"%s.%s.%s" % (message_id.encode(), timestamp.encode(), body)
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return f"v1,{digest}"


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_without_reading_body():
    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/workable",
            "headers": [
                (b"content-length", str(MAX_SIGNED_WEBHOOK_BODY_BYTES + 1).encode()),
            ],
        },
        receive,
    )

    with pytest.raises(HTTPException) as caught:
        await read_signed_webhook_body(request)

    assert caught.value.status_code == 413
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_chunked_body_without_content_length_is_bounded_while_streaming():
    messages = iter(
        [
            {
                "type": "http.request",
                "body": b"x" * MAX_SIGNED_WEBHOOK_BODY_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/resend",
            "headers": [(b"transfer-encoding", b"chunked")],
        },
        receive,
    )

    with pytest.raises(HTTPException) as caught:
        await read_signed_webhook_body(request)

    assert caught.value.status_code == 413


def test_exact_limit_workable_payload_keeps_signature_and_json_behavior(client, monkeypatch):
    secret = "workable-test-secret"
    body = _padded_json(MAX_SIGNED_WEBHOOK_BODY_BYTES)
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(webhook_routes.settings, "WORKABLE_WEBHOOK_SECRET", secret)

    response = client.post(
        "/api/v1/webhooks/workable",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Workable-Signature": signature,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "received", "event_type": "exact-limit"}


@pytest.mark.parametrize("provider", ["workable", "fireflies", "resend", "stripe"])
def test_declared_oversize_reaches_no_signature_or_provider_work(
    client,
    monkeypatch,
    provider,
):
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_STRIPE", False)
    monkeypatch.setattr(webhook_routes.settings, "WORKABLE_WEBHOOK_SECRET", "workable-secret")
    monkeypatch.setattr(webhook_routes.settings, "RESEND_WEBHOOK_SECRET", "resend-secret")
    monkeypatch.setattr(webhook_routes.settings, "STRIPE_WEBHOOK_SECRET", "stripe-secret")

    if provider == "fireflies":
        monkeypatch.setattr(
            webhook_routes,
            "_find_fireflies_org",
            lambda **_kwargs: pytest.fail("database signature lookup must not run"),
        )
    elif provider == "resend":
        monkeypatch.setattr(
            webhook_routes,
            "verify_resend_webhook_signature",
            lambda **_kwargs: pytest.fail("signature verification must not run"),
        )
    elif provider == "stripe":
        monkeypatch.setattr(
            webhook_routes.stripe.Webhook,
            "construct_event",
            lambda *_args, **_kwargs: pytest.fail("Stripe parsing must not run"),
        )

    response = client.post(
        f"/api/v1/webhooks/{provider}",
        content=b"{}",
        headers={"Content-Length": str(MAX_SIGNED_WEBHOOK_BODY_BYTES + 1)},
    )

    assert response.status_code == 413, response.text


def test_valid_fireflies_signature_parses_the_verified_bytes(client, db):
    secret = "fireflies-test-secret"
    db.add(Organization(name="Fireflies Body Limit", fireflies_webhook_secret=secret))
    db.commit()
    body = json.dumps(
        {"eventType": "Meeting ended", "meetingId": "meeting-body-limit"},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=body,
        headers={"Content-Type": "application/json", "x-hub-signature": signature},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ignored", "event_type": "Meeting ended"}


def test_valid_resend_signature_parses_the_verified_bytes(client, monkeypatch):
    secret = "whsec_" + base64.b64encode(b"resend-test-key").decode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    message_id = "msg_body_limit"
    body = b'{"type":"email.delivered","data":{}}'
    monkeypatch.setattr(webhook_routes.settings, "RESEND_WEBHOOK_SECRET", secret)

    response = client.post(
        "/api/v1/webhooks/resend",
        content=body,
        headers={
            "Content-Type": "application/json",
            "svix-id": message_id,
            "svix-timestamp": timestamp,
            "svix-signature": _svix_signature(secret, message_id, timestamp, body),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ignored",
        "reason": "no_email_id",
        "event": "email.delivered",
    }


def test_valid_stripe_signature_reaches_event_handler(client, monkeypatch):
    secret = "whsec_stripe_body_limit"
    timestamp = int(datetime.now(timezone.utc).timestamp())
    body = b'{"id":"evt_body_limit","type":"test.event","data":{"object":{}}}'
    signature = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_STRIPE", False)
    monkeypatch.setattr(webhook_routes.settings, "STRIPE_WEBHOOK_SECRET", secret)

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={timestamp},v1={signature}",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "received"}
