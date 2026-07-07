"""P4: outbound webhooks — subscription CRUD, signed event fan-out, delivery.

``emit_event`` fans one event out to every matching active subscription as a
``WebhookDelivery`` (pending). ``deliver`` POSTs one delivery with an HMAC-SHA256
signature header and records the outcome. Emission + signing + persistence are
deterministic and unit-tested; the network POST is isolated in ``_post`` so it's
trivially monkeypatchable (and can move to a Celery task on staging).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.webhook import (
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    WebhookDelivery,
    WebhookSubscription,
)

_UPDATABLE = {"url", "secret", "event_types", "is_active"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sign_body(secret: str, body: str) -> str:
    """HMAC-SHA256 of the exact serialized body, hex-encoded. The receiver
    recomputes this over the raw request bytes to verify authenticity."""
    return hmac.new(
        (secret or "").encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def serialize_payload(event_type: str, data: dict) -> str:
    """Canonical JSON for signing + sending (sorted keys → stable signature)."""
    return json.dumps(
        {"event": event_type, "data": data}, sort_keys=True, separators=(",", ":")
    )


# --- subscription CRUD ---------------------------------------------------

def list_subscriptions(db: Session, org_id: int) -> List[WebhookSubscription]:
    return (
        db.query(WebhookSubscription)
        .filter(WebhookSubscription.organization_id == org_id)
        .order_by(WebhookSubscription.id)
        .all()
    )


def get_subscription(db: Session, org_id: int, sub_id: int) -> WebhookSubscription:
    sub = (
        db.query(WebhookSubscription)
        .filter(
            WebhookSubscription.id == sub_id,
            WebhookSubscription.organization_id == org_id,
        )
        .first()
    )
    if sub is None:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")
    return sub


def create_subscription(
    db: Session,
    org_id: int,
    *,
    url: str,
    secret: str,
    event_types: Optional[list] = None,
) -> WebhookSubscription:
    if not (url or "").strip():
        raise HTTPException(status_code=422, detail="url is required")
    if not (secret or "").strip():
        raise HTTPException(status_code=422, detail="secret is required")
    sub = WebhookSubscription(
        organization_id=org_id,
        url=url.strip(),
        secret=secret,
        event_types=event_types or [],
        is_active=True,
    )
    db.add(sub)
    db.flush()
    return sub


def update_subscription(
    db: Session, org_id: int, sub_id: int, changes: Dict[str, Any]
) -> WebhookSubscription:
    sub = get_subscription(db, org_id, sub_id)
    for key, value in changes.items():
        if key in _UPDATABLE:
            setattr(sub, key, value)
    db.flush()
    return sub


def delete_subscription(db: Session, org_id: int, sub_id: int) -> None:
    db.delete(get_subscription(db, org_id, sub_id))
    db.flush()


# --- emission + delivery -------------------------------------------------

def _matches(sub: WebhookSubscription, event_type: str) -> bool:
    return not sub.event_types or event_type in sub.event_types


def emit_event(
    db: Session, org_id: int, event_type: str, data: dict
) -> List[WebhookDelivery]:
    """Create a pending delivery for every active subscription in the org that
    subscribes to ``event_type``. Returns the created deliveries (empty if none
    match — emitting is always safe to call)."""
    subs = [
        s
        for s in list_subscriptions(db, org_id)
        if s.is_active and _matches(s, event_type)
    ]
    deliveries = []
    for sub in subs:
        delivery = WebhookDelivery(
            organization_id=org_id,
            subscription_id=sub.id,
            event_type=event_type,
            payload=data,
        )
        db.add(delivery)
        deliveries.append(delivery)
    db.flush()
    return deliveries


def _post(url: str, body: str, signature: str) -> int:
    """POST the signed body; return the HTTP status. Isolated for monkeypatching
    and so a Celery task can own the network call on staging."""
    import httpx

    resp = httpx.post(
        url,
        content=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Taali-Signature": f"sha256={signature}",
            "X-Taali-Event": "",
        },
        timeout=10.0,
    )
    return resp.status_code


def deliver(db: Session, delivery: WebhookDelivery) -> WebhookDelivery:
    """Attempt one delivery: sign, POST, record the outcome. 2xx → delivered,
    anything else (incl. network error) → failed with ``last_error``."""
    sub = delivery.subscription
    body = serialize_payload(delivery.event_type, delivery.payload or {})
    signature = sign_body(sub.secret, body)
    delivery.attempts = (delivery.attempts or 0) + 1
    try:
        status = _post(sub.url, body, signature)
        delivery.response_status = status
        if 200 <= status < 300:
            delivery.status = DELIVERY_DELIVERED
            delivery.delivered_at = _utcnow()
            delivery.last_error = None
        else:
            delivery.status = DELIVERY_FAILED
            delivery.last_error = f"HTTP {status}"
    except Exception as exc:  # network error, timeout, DNS, …
        delivery.status = DELIVERY_FAILED
        delivery.last_error = str(exc)[:500]
    db.flush()
    return delivery
