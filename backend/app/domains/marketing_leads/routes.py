"""Public demo-lead capture: POST /api/v1/public/demo-lead (no auth).

The marketing site's "book a demo" form posts here before routing the
visitor into the demo walkthrough. The lead is not stored — it is
forwarded as an email to hello@taali.ai so a human follows up. Rate
limited per client IP so the open endpoint can't burn Resend quota.
"""

import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from ...platform.brand import BRAND_DOMAIN
from ...platform.config import settings

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1/public", tags=["Marketing leads"])

LEAD_INBOX = f"hello@{BRAND_DOMAIN}"

_WINDOW_SEC = 3600
_MAX_PER_WINDOW = 5
_buckets: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _allow(ip: str) -> bool:
    now = time.time()
    cutoff = now - _WINDOW_SEC
    with _lock:
        bucket = _buckets[ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _MAX_PER_WINDOW:
            return False
        bucket.append(now)
        return True


def reset() -> None:
    """Test helper: flush rate-limit state."""
    with _lock:
        _buckets.clear()


def _client_ip(request: Request) -> str:
    # Railway terminates TLS in front of us; the real client is the first
    # X-Forwarded-For hop, not request.client.
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return (request.client.host if request.client else "") or "unknown"


class DemoLeadIn(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=100)
    volume: str = Field(default="", max_length=50)


def _forward_lead(lead: DemoLeadIn) -> None:
    from ...components.notifications.email_client import EmailService

    if not (settings.RESEND_API_KEY or "").strip():
        logger.info("RESEND_API_KEY not set — demo lead from %s not forwarded", lead.email)
        return
    body = "\n".join([
        f"Email:   {lead.email}",
        f"Name:    {lead.name or '—'}",
        f"Company: {lead.company or '—'}",
        f"Hiring:  {lead.role or '—'}",
        f"Volume:  {lead.volume or '—'}",
    ])
    subject = f"Demo lead: {lead.email}" + (f" ({lead.company})" if lead.company else "")
    EmailService(api_key=settings.RESEND_API_KEY).send_internal_alert(
        to_email=LEAD_INBOX,
        subject=subject,
        text_body=body,
    )


@public_router.post("/demo-lead")
def submit_demo_lead(lead: DemoLeadIn, request: Request, background: BackgroundTasks):
    if not _allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")
    # Forward after the response — the visitor's path into the demo never
    # waits on (or learns about) the Resend call.
    background.add_task(_forward_lead, lead)
    return {"ok": True}
