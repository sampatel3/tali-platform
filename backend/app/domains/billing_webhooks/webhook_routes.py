# Canonical webhook routes for integrations and billing events.
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.secrets import decrypt_integration_secret
from ...services.document_service import sanitize_text_for_storage
from ...services.fireflies_service import verify_fireflies_webhook_signature
from ...services.resend_webhook_service import (
    apply_resend_event,
    verify_resend_webhook_signature,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
logger = logging.getLogger("taali.webhooks")


def _find_fireflies_org(
    *,
    db: Session,
    payload_raw: bytes,
    signature: str | None,
) -> Organization | None:
    orgs = (
        db.query(Organization)
        .filter(Organization.fireflies_webhook_secret.isnot(None))
        .all()
    )
    for org in orgs:
        if verify_fireflies_webhook_signature(
            payload=payload_raw,
            signature=signature,
            secret=decrypt_integration_secret(
                getattr(org, "fireflies_webhook_secret", None),
                allow_plaintext=True,
            ),
        ):
            return org
    return None


@router.post("/workable")
async def workable_webhook(request: Request, db: Session = Depends(get_db)):
    """Verify Workable signatures, but never acknowledge an unprocessed event."""
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
    # No durable inbound-event consumer exists yet. A 2xx here caused Workable
    # to discard stage-change events that Taali had silently dropped. Return an
    # explicit non-success so the provider/operator sees the integration is not
    # active and can retry after a durable inbox is implemented.
    raise HTTPException(
        status_code=501,
        detail="Inbound Workable webhook processing is not implemented; event was not accepted",
    )


async def _accept_fireflies_webhook(
    request: Request,
    *,
    db: Session,
    organization_id: int | None,
) -> dict[str, Any]:
    """Verify and durably enqueue a Fireflies event without provider I/O."""
    from ...services.fireflies_inbox_service import enqueue_event
    from ...tasks.fireflies_tasks import process_fireflies_webhook

    payload_raw = await request.body()
    signature = request.headers.get("x-hub-signature", "")
    if organization_id is None:
        # Backward-compatible legacy endpoint. New Fireflies configuration
        # should use /fireflies/{organization_id}, which is an indexed lookup.
        org = _find_fireflies_org(db=db, payload_raw=payload_raw, signature=signature)
    else:
        org = db.get(Organization, int(organization_id))
        if org is not None and not verify_fireflies_webhook_signature(
            payload=payload_raw,
            signature=signature,
            secret=decrypt_integration_secret(
                getattr(org, "fireflies_webhook_secret", None),
                allow_plaintext=True,
            ),
        ):
            org = None
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid Fireflies webhook signature")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    event_type = sanitize_text_for_storage(str(payload.get("eventType") or "").strip())
    meeting_id = sanitize_text_for_storage(str(payload.get("meetingId") or "").strip())
    if not meeting_id:
        raise HTTPException(status_code=400, detail="meetingId is required")
    if "transcription" not in event_type.lower():
        return {"status": "ignored", "event_type": event_type}

    row, created = enqueue_event(
        db,
        organization_id=org.id,
        meeting_id=meeting_id,
        event_type=event_type,
        payload=payload,
    )
    if created:
        try:
            process_fireflies_webhook.delay(row.id)
        except Exception:
            # The committed inbox row is the authority; the minute sweep will
            # recover a broker outage without making Fireflies retry the event.
            logger.exception("Fireflies inbox dispatch failed inbox_id=%s", row.id)

    # In eager tests the worker may already have completed. In production this
    # normally reports pending/processing while preserving a stable 2xx ack.
    db.expire_all()
    current = db.get(type(row), row.id)
    response: dict[str, Any] = {
        "status": "accepted",
        "inbox_id": row.id,
        "meeting_id": meeting_id,
        "duplicate": not created,
        "processing_status": current.status if current is not None else "pending",
    }
    if current is not None and isinstance(current.result, dict):
        response["result"] = current.result
    return response


@router.post("/fireflies/{organization_id}", status_code=202)
async def fireflies_webhook_for_organization(
    organization_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Canonical O(1) organization-scoped Fireflies webhook endpoint."""
    return await _accept_fireflies_webhook(
        request, db=db, organization_id=organization_id
    )


@router.post("/fireflies", status_code=202)
async def fireflies_webhook(request: Request, db: Session = Depends(get_db)):
    """Legacy route retained for existing Fireflies webhook configurations."""
    return await _accept_fireflies_webhook(request, db=db, organization_id=None)


@router.post("/resend")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Resend email-delivery webhooks (Svix-signed).

    Correlates delivered/opened/bounced/complained events back to the
    assessment by the Resend message id stored at send time, powering the
    invited-candidate delivery tracker. Returns 200 with a small ack so Resend
    doesn't retry, even when the event doesn't match one of our assessments.
    """
    if not settings.RESEND_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Resend webhook secret is not configured")

    body = await request.body()
    if not verify_resend_webhook_signature(
        secret=settings.RESEND_WEBHOOK_SECRET,
        svix_id=request.headers.get("svix-id", ""),
        svix_timestamp=request.headers.get("svix-timestamp", ""),
        svix_signature=request.headers.get("svix-signature", ""),
        body=body,
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    return apply_resend_event(db, payload)


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

    if event_type == "checkout.session.completed":
        # Top-up checkout completed. Grant credits idempotently using the
        # session id as the external ref. Metadata was stamped on the session
        # by ``topup_service.create_topup_checkout_session``.
        from ...services.usage_metering_service import grant_credits as _grant_credits
        from ...models.usage_grant import GRANT_TOPUP

        metadata = data.get("metadata") or {}
        org_id_raw = metadata.get("org_id")
        pack_id = metadata.get("pack_id")
        credits_raw = metadata.get("credits")
        session_id = data.get("id")
        payment_status = data.get("payment_status")

        if (
            org_id_raw
            and pack_id
            and credits_raw
            and session_id
            and payment_status == "paid"
        ):
            try:
                org_id_int = int(org_id_raw)
                credits_int = int(credits_raw)
            except Exception:
                org_id_int = None
                credits_int = 0
            if org_id_int and credits_int > 0:
                _grant_credits(
                    db,
                    organization_id=org_id_int,
                    grant_type=GRANT_TOPUP,
                    credits=credits_int,
                    external_ref=f"stripe:checkout:{session_id}",
                    metadata={"pack_id": pack_id, "session_id": session_id},
                )
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
