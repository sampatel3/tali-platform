"""P4: outbound webhooks — subscription CRUD, emission fan-out, signing, delivery."""
from app.domains.assessments_runtime import webhook_service
from app.domains.assessments_runtime.webhook_service import (
    create_subscription,
    deliver,
    emit_event,
    serialize_payload,
    sign_body,
)
from app.models import Organization, WebhookDelivery
from app.models.user import ROLE_VIEWER, User
from tests.conftest import auth_headers


def _org(db, slug):
    org = Organization(name="Acme", slug=slug)
    db.add(org)
    db.flush()
    return org


def test_sign_body_is_deterministic_and_key_sensitive():
    body = serialize_payload("offer.accepted", {"offer_id": 1})
    a = sign_body("s3cr3t", body)
    assert a == sign_body("s3cr3t", body)  # deterministic
    assert a != sign_body("other", body)  # key-sensitive
    assert len(a) == 64  # hex sha256


def test_emit_fans_out_only_to_matching_active_subs(db):
    org = _org(db, "wh-emit")
    all_events = create_subscription(db, org.id, url="https://a.test/hook", secret="k")
    only_offers = create_subscription(
        db, org.id, url="https://b.test/hook", secret="k",
        event_types=["offer.accepted"],
    )
    inactive = create_subscription(db, org.id, url="https://c.test/hook", secret="k")
    inactive.is_active = False
    db.flush()

    made = emit_event(db, org.id, "application.created", {"application_id": 7})
    sub_ids = {d.subscription_id for d in made}
    # all-events sub gets it; the offers-only sub does not; inactive never does.
    assert all_events.id in sub_ids
    assert only_offers.id not in sub_ids
    assert inactive.id not in sub_ids

    made2 = emit_event(db, org.id, "offer.accepted", {"offer_id": 3})
    sub_ids2 = {d.subscription_id for d in made2}
    assert {all_events.id, only_offers.id} <= sub_ids2


def test_deliver_records_success_and_failure(db, monkeypatch):
    org = _org(db, "wh-deliver")
    sub = create_subscription(db, org.id, url="https://x.test/hook", secret="topsecret")
    [d_ok, d_bad] = emit_event(db, org.id, "offer.sent", {"offer_id": 1}) + emit_event(
        db, org.id, "offer.sent", {"offer_id": 2}
    )

    # Success path — capture the signature header the sender would send.
    seen = {}

    def _fake_post_ok(url, body, signature):
        seen["url"], seen["body"], seen["sig"] = url, body, signature
        return 200

    monkeypatch.setattr(webhook_service, "_post", _fake_post_ok)
    deliver(db, d_ok)
    assert d_ok.status == "delivered" and d_ok.attempts == 1 and d_ok.delivered_at
    # The signature matches HMAC over the exact serialized body.
    assert seen["sig"] == sign_body("topsecret", seen["body"])

    # Failure path — non-2xx is recorded, not raised.
    monkeypatch.setattr(webhook_service, "_post", lambda *a: 500)
    deliver(db, d_bad)
    assert d_bad.status == "failed" and d_bad.response_status == 500

    # Network error is caught and recorded.
    def _boom(*a):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(webhook_service, "_post", _boom)
    d_bad.status = "pending"
    deliver(db, d_bad)
    assert d_bad.status == "failed" and "connection refused" in d_bad.last_error


def test_webhook_api_crud_hides_secret_and_gates_writes(client, db):
    headers, email = auth_headers(client)

    r = client.post(
        "/api/v1/webhooks",
        json={"url": "https://e.test/hook", "secret": "shh", "event_types": ["offer.accepted"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert "secret" not in r.json()  # write-only

    r = client.get("/api/v1/webhooks", headers=headers)
    assert any(s["id"] == sid for s in r.json())

    r = client.patch(f"/api/v1/webhooks/{sid}", json={"is_active": False}, headers=headers)
    assert r.status_code == 200 and r.json()["is_active"] is False

    r = client.get(f"/api/v1/webhooks/{sid}/deliveries", headers=headers)
    assert r.status_code == 200 and r.json() == []

    assert client.delete(f"/api/v1/webhooks/{sid}", headers=headers).status_code == 204

    # Viewer can't manage webhooks.
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_VIEWER
    db.commit()
    assert client.post(
        "/api/v1/webhooks", json={"url": "https://z.test", "secret": "x"}, headers=headers
    ).status_code == 403


def test_webhooks_are_org_scoped(client, db):
    headers, _ = auth_headers(client)
    other = _org(db, "wh-other")
    other_sub = create_subscription(db, other.id, url="https://o.test", secret="k")
    db.commit()
    assert client.get(
        f"/api/v1/webhooks/{other_sub.id}/deliveries", headers=headers
    ).status_code == 404
