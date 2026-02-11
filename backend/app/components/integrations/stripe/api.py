"""Stripe webhook + billing endpoints."""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
import stripe

from ....platform.database import get_db
from ....platform.security import get_current_user
from ....platform.config import settings
from ....models.user import User
from ....models.organization import Organization
from ....models.assessment import Assessment, AssessmentStatus

# -- Webhook router -----------------------------------------------------------

webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@webhook_router.post("/stripe")
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
    elif event_type == "payment_intent.payment_failed":
        pass
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


# -- Billing router ------------------------------------------------------------

billing_router = APIRouter(prefix="/billing", tags=["Billing"])


class CheckoutSessionCreate(BaseModel):
    success_url: str
    cancel_url: str


@billing_router.get("/usage")
def get_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return usage history for the current org."""
    org_id = current_user.organization_id
    if not org_id:
        return {"usage": [], "total_cost": 0}
    assessments = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.organization_id == org_id,
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.completed_at != None,
        )
        .order_by(Assessment.completed_at.desc())
        .limit(100)
        .all()
    )
    cost_per = 25
    usage = []
    for a in assessments:
        completed_at = a.completed_at
        date_str = completed_at.strftime("%b %d, %Y") if completed_at else ""
        candidate_name = (a.candidate.full_name or a.candidate.email) if a.candidate else "\u2014"
        task_name = a.task.name if a.task else "\u2014"
        usage.append({
            "date": date_str,
            "candidate": candidate_name,
            "task": task_name,
            "cost": f"\u00a3{cost_per}",
            "assessment_id": a.id,
        })
    return {"usage": usage, "total_cost": len(usage) * cost_per}


@billing_router.post("/checkout-session")
def create_checkout_session(
    body: CheckoutSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Checkout Session for one assessment."""
    if settings.MVP_DISABLE_STRIPE:
        raise HTTPException(status_code=503, detail="Billing is disabled for MVP pilot")
    import stripe as stripe_lib
    if not settings.STRIPE_API_KEY or not settings.STRIPE_API_KEY.startswith("sk_"):
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    stripe_lib.api_key = settings.STRIPE_API_KEY

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    customer_id = org.stripe_customer_id
    if not customer_id:
        customer = stripe_lib.Customer.create(
            email=current_user.email,
            name=current_user.full_name or current_user.email,
            metadata={"org_id": str(org.id), "platform": "tali"},
        )
        customer_id = customer.id
        org.stripe_customer_id = customer_id
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to store Stripe customer")

    try:
        session = stripe_lib.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {"name": "TALI Assessment", "description": "One technical assessment"},
                    "unit_amount": 2500,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            metadata={"org_id": str(org.id), "type": "assessment"},
        )
        return {"url": session.url}
    except Exception as e:
        import logging as _logging
        _logging.getLogger("tali.billing").exception("Stripe checkout session error")
        raise HTTPException(status_code=502, detail="Payment service error. Please try again.")
