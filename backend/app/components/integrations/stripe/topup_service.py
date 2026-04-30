"""Stripe Checkout (one-time payment) wrapper for credit pack top-ups.

Replaces the Lemon Squeezy checkout flow. Each pack maps to a single
Stripe ``Price`` configured in the dashboard. The pack metadata travels
on the ``Session`` so the webhook can attribute the purchase back to the
correct org and credit count.
"""
from __future__ import annotations

from typing import Optional

import stripe

from ....platform.config import settings
from ....services.pricing_service import CreditPack, resolve_pack


class StripeTopupError(Exception):
    """Raised when checkout-session creation fails."""


def create_topup_checkout_session(
    *,
    org_id: int,
    customer_email: str,
    pack_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session for a credit-pack top-up. Returns
    the checkout URL the frontend should redirect to.

    The session uses ``mode=payment`` (one-time, not subscription) and
    ``payment_intent_data.metadata`` so the webhook handler can recover
    ``org_id`` and ``pack_id`` after the customer completes payment.
    """
    if not settings.STRIPE_API_KEY:
        raise StripeTopupError("STRIPE_API_KEY is not configured")

    pack = resolve_pack(pack_id)
    if pack is None:
        raise StripeTopupError(f"unknown pack_id: {pack_id}")

    stripe.api_key = settings.STRIPE_API_KEY

    metadata = {
        "org_id": str(org_id),
        "pack_id": pack.pack_id,
        "credits": str(pack.credits_granted),
        "source": "taali_topup",
    }

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=customer_email,
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Taali credits — {pack.label}",
                            "description": (
                                f"${pack.price_usd:.0f} of platform credits"
                                + (f" (+{pack.bonus_pct}% bonus)" if pack.bonus_pct else "")
                            ),
                        },
                        "unit_amount": pack.price_usd_cents,
                    },
                    "quantity": 1,
                }
            ],
            metadata=metadata,
            payment_intent_data={"metadata": metadata},
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except stripe.error.StripeError as exc:
        raise StripeTopupError(f"stripe error: {exc}") from exc

    url = getattr(session, "url", None) or session.get("url") if isinstance(session, dict) else None
    if not url:
        raise StripeTopupError("Stripe Checkout returned no url")
    return str(url)


def derive_pack_from_event_metadata(metadata: dict | None) -> Optional[CreditPack]:
    if not metadata:
        return None
    pack_id = str(metadata.get("pack_id") or "").strip()
    if not pack_id:
        return None
    return resolve_pack(pack_id)
