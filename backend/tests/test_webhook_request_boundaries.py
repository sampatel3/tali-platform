"""Provider webhook request-boundary regressions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from app.domains.billing_webhooks import webhook_routes
from app.models.fireflies_webhook_inbox import FirefliesWebhookInbox
from app.models.organization import Organization
from app.services import fireflies_inbox_service
from app.services.fireflies_service import verify_fireflies_webhook_signature
from app.platform.secrets import encrypt_integration_secret
from app.services.webhook_request import (
    MAX_WEBHOOK_BODY_BYTES,
    read_bounded_webhook_body,
)


def _oversized_body() -> bytes:
    return b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)


class _ChunkedRequest:
    headers: dict[str, str] = {}

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_bounded_reader_enforces_limit_without_content_length():
    request = _ChunkedRequest([b"a" * 600_000, b"b" * 500_000])

    with pytest.raises(HTTPException) as exc_info:
        await read_bounded_webhook_body(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_bounded_reader_preserves_payload_at_exact_limit():
    request = _ChunkedRequest([b"a" * 600_000, b"b" * 448_576])

    body = await read_bounded_webhook_body(request)  # type: ignore[arg-type]

    assert len(body) == MAX_WEBHOOK_BODY_BYTES
    assert body[:1] == b"a"
    assert body[-1:] == b"b"


def test_workable_oversize_is_rejected_before_signature_work(client, monkeypatch):
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(webhook_routes.settings, "WORKABLE_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        webhook_routes.hmac,
        "new",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("signature work ran")
        ),
    )

    response = client.post(
        "/api/v1/webhooks/workable",
        content=_oversized_body(),
        headers={"X-Workable-Signature": "0" * 64},
    )

    assert response.status_code == 413


def test_fireflies_oversize_is_rejected_before_org_scan(client, monkeypatch):
    calls = {"org_scan": 0, "decrypt": 0, "enqueue": 0}

    def find_org(**_kwargs):
        calls["org_scan"] += 1
        return None

    def decrypt(*_args, **_kwargs):
        calls["decrypt"] += 1
        return "secret"

    def enqueue(*_args, **_kwargs):
        calls["enqueue"] += 1
        raise AssertionError("enqueue ran")

    monkeypatch.setattr(webhook_routes, "_find_fireflies_org", find_org)
    monkeypatch.setattr(webhook_routes, "decrypt_integration_secret", decrypt)
    monkeypatch.setattr(fireflies_inbox_service, "enqueue_event", enqueue)

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=_oversized_body(),
        headers={"x-hub-signature": "0" * 64},
    )

    assert response.status_code == 413
    assert calls == {"org_scan": 0, "decrypt": 0, "enqueue": 0}


def test_fireflies_uses_small_provider_envelope_limit_before_org_scan(
    client,
    monkeypatch,
):
    calls = {"org_scan": 0}

    def find_org(**_kwargs):
        calls["org_scan"] += 1
        return None

    monkeypatch.setattr(
        webhook_routes,
        "_find_fireflies_org",
        find_org,
    )

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=b"x" * (webhook_routes.FIREFLIES_WEBHOOK_MAX_BODY_BYTES + 1),
        headers={"x-hub-signature": "0" * 64},
    )

    assert response.status_code == 413
    assert calls == {"org_scan": 0}


def test_legacy_fireflies_remains_compatible_beyond_sixteen_configured_orgs(
    client,
    db,
    monkeypatch,
):
    from app.tasks.fireflies_tasks import process_fireflies_webhook

    organizations = []
    for index in range(17):
        organization = Organization(
            name=f"Legacy Fireflies {index}",
            slug=f"legacy-fireflies-{index}",
            fireflies_webhook_secret=f"secret-{index}",
        )
        organizations.append(organization)
        db.add(organization)
    db.commit()
    target = organizations[-1]
    monkeypatch.setattr(process_fireflies_webhook, "delay", lambda _inbox_id: None)
    monkeypatch.setattr(
        webhook_routes,
        "decrypt_integration_secret",
        lambda value, **_kwargs: value,
    )
    payload = b'{"meetingId":"legacy-17","eventType":"Transcription completed"}'
    digest = hmac.new(b"secret-16", payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=payload,
        headers={"x-hub-signature": f"sha256={digest}"},
    )

    assert response.status_code == 202, response.text
    row = db.query(FirefliesWebhookInbox).one()
    assert row.organization_id == target.id
    assert row.meeting_id == "legacy-17"


def test_legacy_fireflies_rejects_ambiguous_shared_plaintext_secret(
    client,
    db,
):
    shared_secret = "shared-fireflies-webhook-secret"
    first_ciphertext = encrypt_integration_secret(shared_secret)
    second_ciphertext = encrypt_integration_secret(shared_secret)
    assert first_ciphertext != second_ciphertext
    db.add_all(
        [
            Organization(
                name="Legacy Fireflies duplicate A",
                slug="legacy-fireflies-duplicate-a",
                fireflies_webhook_secret=first_ciphertext,
            ),
            Organization(
                name="Legacy Fireflies duplicate B",
                slug="legacy-fireflies-duplicate-b",
                fireflies_webhook_secret=second_ciphertext,
            ),
        ]
    )
    db.commit()
    payload = b'{"meetingId":"ambiguous-secret","eventType":"meeting.transcribed"}'
    digest = hmac.new(shared_secret.encode(), payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=payload,
        headers={"x-hub-signature": f"sha256={digest}"},
    )

    assert response.status_code == 401
    assert db.query(FirefliesWebhookInbox).count() == 0


@pytest.mark.parametrize("replacement", [None, "rotated-secret"])
def test_legacy_fireflies_reverifies_locked_current_secret(
    db,
    monkeypatch,
    replacement,
):
    org = Organization(
        name="Legacy Fireflies rotation",
        slug="legacy-fireflies-rotation",
        fireflies_webhook_secret="original-secret",
    )
    db.add(org)
    db.commit()
    payload = b'{"meetingId":"rotation"}'
    digest = hmac.new(b"original-secret", payload, hashlib.sha256).hexdigest()
    calls = {"count": 0}

    def verify_with_rotation(**kwargs):
        calls["count"] += 1
        verified = verify_fireflies_webhook_signature(**kwargs)
        if calls["count"] == 1:
            org.fireflies_webhook_secret = replacement
        return verified

    monkeypatch.setattr(
        webhook_routes,
        "decrypt_integration_secret",
        lambda value, **_kwargs: value,
    )
    monkeypatch.setattr(
        webhook_routes,
        "verify_fireflies_webhook_signature",
        verify_with_rotation,
    )

    matched = webhook_routes._find_fireflies_org(
        db=db,
        payload_raw=payload,
        signature=f"sha256={digest}",
    )

    assert matched is None
    assert calls == {"count": 2}


@pytest.mark.parametrize("replacement", [None, "rotated-secret"])
def test_scoped_fireflies_locks_and_reloads_current_secret(
    db,
    monkeypatch,
    replacement,
):
    org = Organization(
        name="Scoped Fireflies rotation",
        slug=f"scoped-fireflies-rotation-{replacement or 'cleared'}",
        fireflies_webhook_secret="original-secret",
    )
    db.add(org)
    db.flush()
    organization_id = int(org.id)
    db.commit()

    # Commit the rotation in another transaction, then reproduce a dependency
    # session whose identity map still contains the credential it saw earlier.
    rotation_session = sessionmaker(bind=db.get_bind())
    with rotation_session.begin() as rotation_db:
        stored = rotation_db.get(Organization, organization_id)
        assert stored is not None
        stored.fireflies_webhook_secret = replacement
    set_committed_value(org, "fireflies_webhook_secret", "original-secret")

    compiled: list[str] = []
    execute = db.execute

    def capture_lock(statement, *args, **kwargs):
        compiled.append(str(statement.compile(dialect=postgresql.dialect())))
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", capture_lock)
    monkeypatch.setattr(
        webhook_routes,
        "decrypt_integration_secret",
        lambda value, **_kwargs: value,
    )
    payload = b'{"meetingId":"scoped-rotation"}'
    old_digest = hmac.new(
        b"original-secret",
        payload,
        hashlib.sha256,
    ).hexdigest()

    matched = webhook_routes._lock_and_verify_scoped_fireflies_org(
        db=db,
        organization_id=organization_id,
        payload_raw=payload,
        signature=f"sha256={old_digest}",
    )

    assert matched is None
    # The invalid preliminary match intentionally stays a projection-only read;
    # it must not refresh an ORM entity or request the row lock.
    assert org.fireflies_webhook_secret == "original-secret"
    assert all("FOR UPDATE" not in sql.upper() for sql in compiled)

    if replacement is not None:
        db.rollback()
        set_committed_value(org, "fireflies_webhook_secret", "original-secret")
        current_digest = hmac.new(
            replacement.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        matched = webhook_routes._lock_and_verify_scoped_fireflies_org(
            db=db,
            organization_id=organization_id,
            payload_raw=payload,
            signature=f"sha256={current_digest}",
        )
        assert matched is not None
        assert matched.id == organization_id
        assert matched.fireflies_webhook_secret == replacement
        lock_sql = next(sql for sql in compiled if "FOR UPDATE" in sql.upper())
        assert "WHERE organizations.id =" in lock_sql


def test_scoped_fireflies_invalid_signature_never_requests_row_lock(
    db,
    monkeypatch,
):
    org = Organization(
        name="Scoped Fireflies invalid signature",
        slug="scoped-fireflies-invalid-signature",
        fireflies_webhook_secret="configured-secret",
    )
    db.add(org)
    db.commit()
    organization_id = int(org.id)
    statements: list[str] = []
    execute = db.execute

    def capture_statement(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", capture_statement)
    monkeypatch.setattr(
        webhook_routes,
        "decrypt_integration_secret",
        lambda value, **_kwargs: value,
    )

    matched = webhook_routes._lock_and_verify_scoped_fireflies_org(
        db=db,
        organization_id=organization_id,
        payload_raw=b'{"meetingId":"invalid-signature"}',
        signature="0" * 64,
    )

    assert matched is None
    assert len(statements) == 1
    assert "FOR UPDATE" not in statements[0].upper()


def test_resend_oversize_is_rejected_before_signature_work(client, monkeypatch):
    monkeypatch.setattr(webhook_routes.settings, "RESEND_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        webhook_routes,
        "verify_resend_webhook_signature",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("signature work ran")
        ),
    )

    response = client.post(
        "/api/v1/webhooks/resend",
        content=_oversized_body(),
    )

    assert response.status_code == 413


def test_resend_valid_signed_payload_is_unchanged(client, monkeypatch):
    secret = "whsec_" + base64.b64encode(b"webhook-secret").decode()
    monkeypatch.setattr(webhook_routes.settings, "RESEND_WEBHOOK_SECRET", secret)
    body = b'{"type":"email.delivered","data":{"email_id":"unknown"}}'
    message_id = "msg_webhook_boundary"
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed = b"%s.%s.%s" % (message_id.encode(), timestamp.encode(), body)
    signature = base64.b64encode(
        hmac.new(b"webhook-secret", signed, hashlib.sha256).digest()
    ).decode()

    response = client.post(
        "/api/v1/webhooks/resend",
        content=body,
        headers={
            "content-type": "application/json",
            "svix-id": message_id,
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ignored",
        "reason": "no_matching_assessment",
        "event": "email.delivered",
    }


def test_stripe_oversize_is_rejected_before_provider_parse(client, monkeypatch):
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_STRIPE", False)
    monkeypatch.setattr(webhook_routes.settings, "STRIPE_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(
        webhook_routes.stripe.Webhook,
        "construct_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider parse ran")
        ),
    )

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=_oversized_body(),
        headers={"Stripe-Signature": "test"},
    )

    assert response.status_code == 413


def test_invalid_fireflies_signature_shape_does_no_org_or_inbox_work(
    client, monkeypatch
):
    calls = {"org_scan": 0, "decrypt": 0, "enqueue": 0}

    def find_org(**_kwargs):
        calls["org_scan"] += 1
        return None

    def decrypt(*_args, **_kwargs):
        calls["decrypt"] += 1
        return "secret"

    def enqueue(*_args, **_kwargs):
        calls["enqueue"] += 1
        raise AssertionError("enqueue ran")

    monkeypatch.setattr(webhook_routes, "_find_fireflies_org", find_org)
    monkeypatch.setattr(webhook_routes, "decrypt_integration_secret", decrypt)
    monkeypatch.setattr(fireflies_inbox_service, "enqueue_event", enqueue)

    response = client.post(
        "/api/v1/webhooks/fireflies",
        content=b"{}",
        headers={"x-hub-signature": "not-a-signature"},
    )

    assert response.status_code == 401
    assert "not-a-signature" not in response.text
    assert calls == {"org_scan": 0, "decrypt": 0, "enqueue": 0}


def test_fireflies_v2_signature_and_payload_use_existing_durable_inbox(
    client, db, monkeypatch
):
    from app.tasks.fireflies_tasks import process_fireflies_webhook

    org = Organization(
        name="Fireflies V2 org",
        slug="fireflies-v2-org",
        fireflies_webhook_secret="fireflies-secret",
    )
    db.add(org)
    db.commit()
    monkeypatch.setattr(process_fireflies_webhook, "delay", lambda _inbox_id: None)
    monkeypatch.setattr(
        webhook_routes,
        "decrypt_integration_secret",
        lambda _value, **_kwargs: "fireflies-secret",
    )
    payload = {
        "event": "meeting.transcribed",
        "timestamp": 1_752_842_400_000,
        "meeting_id": "meeting-v2-1",
    }
    raw = json.dumps(payload).encode()
    digest = hmac.new(b"fireflies-secret", raw, hashlib.sha256).hexdigest()

    response = client.post(
        f"/api/v1/webhooks/fireflies/{org.id}",
        content=raw,
        headers={
            "content-type": "application/json",
            "x-hub-signature": f"sha256={digest}",
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["meeting_id"] == "meeting-v2-1"
    row = db.query(FirefliesWebhookInbox).one()
    assert row.organization_id == org.id
    assert row.meeting_id == "meeting-v2-1"
    assert row.event_type == "meeting.transcribed"


def test_fireflies_signature_accepts_documented_prefix_and_legacy_bare_digest():
    payload = b'{"meeting_id":"m-1"}'
    digest = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()

    assert verify_fireflies_webhook_signature(
        payload=payload,
        signature=f"sha256={digest}",
        secret="secret",
    )
    assert verify_fireflies_webhook_signature(
        payload=payload,
        signature=digest,
        secret="secret",
    )
    assert not verify_fireflies_webhook_signature(
        payload=payload,
        signature=f"sha256={digest.upper()}",
        secret="secret",
    )
    assert not verify_fireflies_webhook_signature(
        payload=payload,
        signature=f"sha256={digest}extra",
        secret="secret",
    )
