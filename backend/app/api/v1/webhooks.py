from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
import hmac
import hashlib
import stripe
from ...core.database import get_db
from ...core.config import settings

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/workable")
async def workable_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Workable webhooks."""
    signature = request.headers.get("X-Workable-Signature", "")
    body = await request.body()

    expected = hmac.new(
        settings.WORKABLE_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    data = await request.json()
    event_type = data.get("type")

    if event_type == "candidate_stage_changed":
        from ...models.organization import Organization
        subdomain = data.get("account", {}).get("subdomain")
        org = db.query(Organization).filter(Organization.workable_subdomain == subdomain).first()
        if org and org.workable_config and org.workable_config.get("auto_send_on_stage"):
            target_stage = org.workable_config.get("auto_send_stage")
            if data.get("stage") == target_stage:
                # TODO: create assessment via Celery task
                pass

    return {"status": "received"}


@router.post("/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Stripe webhooks."""
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "payment_intent.succeeded":
        # Log successful payment
        pass
    elif event["type"] == "customer.subscription.deleted":
        # Handle subscription cancellation
        pass

    return {"status": "received"}
