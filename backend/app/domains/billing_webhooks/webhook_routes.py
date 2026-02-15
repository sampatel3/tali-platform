# Canonical webhook routes for integrations and billing events.
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...components.integrations.lemon.service import LemonService
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.database import get_db
from ...services.credit_ledger_service import (
    append_credit_ledger_entry,
    resolve_pack,
    resolve_pack_by_variant,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _nested_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


@router.post("/workable")
async def workable_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Workable webhooks (signature verification + receipt ack)."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    if not settings.WORKABLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Workable webhook secret is not configured")
    signature = request.headers.get("X-Workable-Signature", "")
    body = await request.body()

    expected = hmac.new(
        settings.WORKABLE_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    payload = await request.json()
    return {"status": "received", "event_type": payload.get("type")}


@router.post("/lemon")
async def lemon_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Lemon Squeezy webhooks and credit org balances."""
    if settings.MVP_DISABLE_LEMON:
        raise HTTPException(status_code=503, detail="Lemon integration is disabled for MVP")
    if not settings.LEMON_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Lemon webhook secret is not configured")

    payload_raw = await request.body()
    signature = request.headers.get("X-Signature", "")
    if not LemonService.verify_signature(payload=payload_raw, signature=signature, secret=settings.LEMON_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event_name = _nested_get(payload, "meta", "event_name") or payload.get("event_name")
    data = payload.get("data") or {}
    attributes = data.get("attributes") or {}

    # Process payment-complete style events only.
    status = str(attributes.get("status") or "").lower()
    if event_name not in {"order_created", "order_paid"} and status not in {"paid"}:
        return {"status": "ignored", "event_name": event_name}

    custom = (
        attributes.get("custom_data")
        or _nested_get(attributes, "checkout_data", "custom")
        or _nested_get(payload, "meta", "custom_data")
        or {}
    )
    org_id_raw = custom.get("org_id")
    if not org_id_raw:
        # Fallback: infer from first order item custom payloads if present.
        first_item = _nested_get(attributes, "first_order_item") or {}
        org_id_raw = (
            _nested_get(first_item, "custom_data", "org_id")
            or _nested_get(first_item, "checkout_data", "custom", "org_id")
        )
    if not org_id_raw:
        raise HTTPException(status_code=400, detail="org_id missing in webhook payload")

    try:
        org_id = int(org_id_raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid org_id in webhook payload") from exc

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    pack_id = custom.get("pack_id")
    credits_raw = custom.get("credits")
    credits: int | None = None
    if credits_raw is not None:
        try:
            credits = int(credits_raw)
        except Exception:
            credits = None
    if credits is None and pack_id:
        pack = resolve_pack(str(pack_id))
        if pack:
            credits = int(pack["credits"])
    if credits is None:
        variant_id = (
            _nested_get(attributes, "first_order_item", "variant_id")
            or _nested_get(data, "relationships", "variant", "data", "id")
        )
        if variant_id:
            resolved = resolve_pack_by_variant(str(variant_id))
            if resolved:
                pack_id, pack = resolved
                credits = int(pack["credits"])
    if not credits or credits <= 0:
        raise HTTPException(status_code=400, detail="Unable to resolve credits for webhook event")

    order_ref = str(data.get("id") or _nested_get(payload, "meta", "event_id") or "")
    if not order_ref:
        order_ref = str(_nested_get(attributes, "identifier") or "")
    if not order_ref:
        raise HTTPException(status_code=400, detail="Unable to resolve webhook order reference")
    external_ref = f"lemon:order:{order_ref}"

    _, created = append_credit_ledger_entry(
        db,
        organization=org,
        delta=credits,
        reason="lemon_purchase",
        external_ref=external_ref,
        metadata={
            "event_name": event_name,
            "pack_id": pack_id,
            "credits": credits,
        },
    )
    if created:
        db.commit()
    return {"status": "received", "credited": bool(created), "credits": credits}


@router.post("/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Stripe webhooks."""
    if settings.MVP_DISABLE_STRIPE:
        raise HTTPException(status_code=503, detail="Stripe integration is disabled for MVP")
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured")
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event.get("data", {}).get("object", {})

    if event_type == "payment_intent.succeeded":
        org_id = (data.get("metadata") or {}).get("org_id")
        if org_id:
            org = db.query(Organization).filter(Organization.id == int(org_id)).first()
            if org:
                org.assessments_used = max((org.assessments_used or 0) - 1, 0)
                db.commit()
    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id:
            org = db.query(Organization).filter(Organization.stripe_customer_id == customer_id).first()
            if org:
                org.plan = "pay_per_use"
                org.stripe_subscription_id = None
                db.commit()
    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        if customer_id:
            org = db.query(Organization).filter(Organization.stripe_customer_id == customer_id).first()
            if org:
                org.stripe_subscription_id = data.get("id")
                org.plan = "monthly" if data.get("status") == "active" else "pay_per_use"
                db.commit()

    return {"status": "received"}
