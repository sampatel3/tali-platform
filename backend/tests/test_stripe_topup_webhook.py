"""Stripe webhook contract for one-time credit top-ups."""

from app.domains.billing_webhooks import webhook_routes
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.usage_grant import UsageGrant
from app.models.user import User
from tests.conftest import auth_headers


def _organization_for(db, email: str) -> Organization:
    user = db.query(User).filter(User.email == email).one()
    return db.query(Organization).filter(Organization.id == user.organization_id).one()


def _configure_stripe_webhook(monkeypatch, event: dict) -> None:
    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_STRIPE", False)
    monkeypatch.setattr(
        webhook_routes.settings,
        "STRIPE_WEBHOOK_SECRET",
        "whsec_test_topup_contract",
    )
    monkeypatch.setattr(
        webhook_routes.stripe.Webhook,
        "construct_event",
        lambda _payload, _signature, _secret: event,
    )


def test_checkout_completed_grants_topup_once_across_webhook_replay(
    client, db, monkeypatch
):
    _, email = auth_headers(
        client,
        email="stripe-topup-webhook@example.com",
        organization_name="Stripe top-up webhook",
    )
    org = _organization_for(db, email)
    starting_balance = int(org.credits_balance or 0)
    session_id = "cs_test_idempotent_topup"
    credits = 123_456
    _configure_stripe_webhook(
        monkeypatch,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_id,
                    "payment_status": "paid",
                    "metadata": {
                        "org_id": str(org.id),
                        "pack_id": "starter_test",
                        "credits": str(credits),
                    },
                }
            },
        },
    )

    responses = [
        client.post(
            "/api/v1/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "test-signature"},
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json() == {"status": "received"} for response in responses)
    db.expire_all()
    org = _organization_for(db, email)
    external_ref = f"stripe:checkout:{session_id}"
    grants = db.query(UsageGrant).filter(UsageGrant.external_ref == external_ref).all()
    ledger = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == external_ref)
        .all()
    )

    assert int(org.credits_balance or 0) == starting_balance + credits
    assert len(grants) == 1
    assert grants[0].credits_granted == credits
    assert grants[0].grant_type == "topup"
    assert len(ledger) == 1
    assert ledger[0].delta == credits


def test_payment_intent_event_cannot_duplicate_checkout_credit(
    client, db, monkeypatch
):
    _, email = auth_headers(
        client,
        email="stripe-payment-intent@example.com",
        organization_name="Stripe payment intent",
    )
    org = _organization_for(db, email)
    starting_balance = int(org.credits_balance or 0)
    _configure_stripe_webhook(
        monkeypatch,
        {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_not_a_grant_source",
                    "metadata": {
                        "org_id": str(org.id),
                        "pack_id": "starter_test",
                        "credits": "999999",
                    },
                }
            },
        },
    )

    response = client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    db.expire_all()
    assert int(_organization_for(db, email).credits_balance or 0) == starting_balance
    assert (
        db.query(UsageGrant)
        .filter(UsageGrant.external_ref == "stripe:checkout:pi_test_not_a_grant_source")
        .count()
        == 0
    )
