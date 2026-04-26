# Canonical webhook routes for integrations and billing events.
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...components.integrations.lemon.service import LemonService
from ...models.application_interview import ApplicationInterview
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.secrets import decrypt_text
from ...services.document_service import sanitize_json_for_storage, sanitize_text_for_storage
from ...services.fireflies_service import (
    FirefliesService,
    attach_fireflies_match_metadata,
    normalize_email,
    normalized_transcript_bundle,
    verify_fireflies_webhook_signature,
)
from ...services.interview_support_service import refresh_application_interview_support
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
            secret=getattr(org, "fireflies_webhook_secret", None),
        ):
            return org
    return None


def _candidate_emails_from_transcript(bundle: dict[str, Any]) -> list[str]:
    emails: list[str] = []
    organizer_email = normalize_email(bundle.get("organizer_email"))
    host_email = normalize_email(bundle.get("host_email"))
    invite_email = normalize_email(_nested_get(bundle, "taali_match", "fireflies_invite_email"))
    excluded_emails = {item for item in {organizer_email, host_email, invite_email} if item}
    for raw in bundle.get("participants") or []:
        value = normalize_email(raw)
        if value and value not in excluded_emails and value not in emails:
            emails.append(value)
    raw_payload = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
    attendees = raw_payload.get("meeting_attendees") if isinstance(raw_payload.get("meeting_attendees"), list) else []
    for item in attendees:
        if not isinstance(item, dict):
            continue
        value = normalize_email(item.get("email"))
        if value and value not in excluded_emails and value not in emails:
            emails.append(value)
    return emails


def _candidate_applications_for_fireflies(
    *,
    db: Session,
    org: Organization,
    candidate_emails: list[str],
) -> list[CandidateApplication]:
    if not candidate_emails:
        return []
    return (
        db.query(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            Candidate.email.in_(candidate_emails),
        )
        .all()
    )


def _link_fireflies_interview(
    *,
    db: Session,
    org: Organization,
    app: CandidateApplication,
    stage: str,
    bundle: dict[str, Any],
) -> ApplicationInterview:
    provider_meeting_id = sanitize_text_for_storage(str(bundle.get("provider_meeting_id") or "").strip()) or None
    interview = (
        db.query(ApplicationInterview)
        .filter(
            ApplicationInterview.organization_id == org.id,
            ApplicationInterview.application_id == app.id,
            ApplicationInterview.provider == "fireflies",
            ApplicationInterview.provider_meeting_id == provider_meeting_id,
        )
        .first()
    )
    if interview is None:
        interview = ApplicationInterview(
            organization_id=org.id,
            application_id=app.id,
            stage=stage,
            source="fireflies",
            provider="fireflies",
            provider_meeting_id=provider_meeting_id,
        )
        db.add(interview)
        db.flush()
    interview.stage = stage
    interview.source = "fireflies"
    interview.provider = "fireflies"
    interview.provider_meeting_id = provider_meeting_id
    interview.provider_url = bundle.get("provider_url")
    interview.status = "completed"
    interview.transcript_text = bundle.get("transcript_text")
    interview.summary = bundle.get("summary")
    interview.speakers = bundle.get("speakers") if isinstance(bundle.get("speakers"), list) else []
    interview.provider_payload = attach_fireflies_match_metadata(
        bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {},
        invite_email=getattr(org, "fireflies_invite_email", None),
        linked_via="webhook_auto_match",
        matched_application_id=app.id,
    )
    interview.meeting_date = bundle.get("meeting_date")
    interview.linked_at = datetime.now(timezone.utc)
    refresh_application_interview_support(app, organization=org)
    return interview


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


@router.post("/fireflies")
async def fireflies_webhook(request: Request, db: Session = Depends(get_db)):
    payload_raw = await request.body()
    signature = request.headers.get("x-hub-signature", "")
    org = _find_fireflies_org(db=db, payload_raw=payload_raw, signature=signature)
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid Fireflies webhook signature")

    payload = await request.json()
    event_type = sanitize_text_for_storage(str(payload.get("eventType") or "").strip())
    meeting_id = sanitize_text_for_storage(str(payload.get("meetingId") or "").strip())
    if not meeting_id:
        raise HTTPException(status_code=400, detail="meetingId is required")
    if "transcription" not in event_type.lower():
        return {"status": "ignored", "event_type": event_type}

    api_key = decrypt_text(getattr(org, "fireflies_api_key_encrypted", None), settings.SECRET_KEY)
    if not api_key:
        raise HTTPException(status_code=503, detail="Fireflies API key is not configured")
    service = FirefliesService(api_key=api_key)
    try:
        transcript = service.get_transcript(meeting_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Fireflies transcript: {exc}") from exc
    if not transcript:
        return {"status": "ignored", "reason": "transcript_not_found", "meeting_id": meeting_id}

    bundle = normalized_transcript_bundle(transcript)
    organizer_email = str(bundle.get("organizer_email") or "").strip().lower()
    configured_owner = str(getattr(org, "fireflies_owner_email", None) or "").strip().lower()
    if configured_owner and organizer_email and configured_owner != organizer_email:
        return {
            "status": "ignored",
            "reason": "owner_mismatch",
            "meeting_id": meeting_id,
        }

    bundle["taali_match"] = {
        "fireflies_invite_email": normalize_email(getattr(org, "fireflies_invite_email", None)),
    }
    candidate_emails = _candidate_emails_from_transcript(bundle)
    matches = _candidate_applications_for_fireflies(db=db, org=org, candidate_emails=candidate_emails)
    if len(matches) != 1:
        return {
            "status": "review_required",
            "reason": "ambiguous_match" if matches else "no_match",
            "meeting_id": meeting_id,
            "candidate_emails": candidate_emails,
            "candidate_application_ids": [app.id for app in matches],
        }

    app = matches[0]
    stage = "tech_stage_2" if app.pipeline_stage == "review" else "screening"
    interview = _link_fireflies_interview(
        db=db,
        org=org,
        app=app,
        stage=stage,
        bundle=bundle,
    )
    db.commit()
    return {
        "status": "linked",
        "meeting_id": meeting_id,
        "application_id": app.id,
        "interview_id": interview.id,
    }


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
