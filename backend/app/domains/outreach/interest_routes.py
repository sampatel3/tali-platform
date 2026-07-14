"""Public outreach CTA redirect and scanner-safe click tracking.

Each outreach email's CTA points at ``/api/v1/public/outreach/interest/{token}``.
A GET marks only ``clicked`` and redirects to the campaign's validated apply
destination. Mail-security scanners follow GET links, so a GET must never
fabricate the stronger ``interested`` signal. Actual application submission or
a future explicit POST confirmation owns that conversion.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...models.outreach_campaign import (
    MESSAGE_STATUS_CLICKED,
    MESSAGE_STATUS_RANK,
    OutreachCampaign,
    OutreachMessage,
)
from ...platform.config import settings
from ...platform.database import get_db

public_router = APIRouter(prefix="/api/v1/public", tags=["Outreach interest (public)"])


def _thanks_url() -> str:
    """FE thanks page (relative when FRONTEND_URL is empty)."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/outreach/thanks" if base else "/outreach/thanks"


def _job_page_url(token: str) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/job/{token}" if base else f"/job/{token}"


@public_router.get("/outreach/interest/{interest_token}")
def capture_interest(interest_token: str, db: Session = Depends(get_db)):
    message = (
        db.query(OutreachMessage)
        .filter(OutreachMessage.interest_token == interest_token)
        .first()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Invalid interest link")

    now = datetime.now(timezone.utc)
    # Ratchet only to clicked. A security scanner may issue this GET; it cannot
    # prove candidate interest or application intent.
    current_rank = MESSAGE_STATUS_RANK.get(message.status, 0)
    if current_rank <= MESSAGE_STATUS_RANK[MESSAGE_STATUS_CLICKED]:
        if message.status in MESSAGE_STATUS_RANK:  # only ratchet from tracking states
            message.status = MESSAGE_STATUS_CLICKED
    message.clicked_at = message.clicked_at or now

    db.commit()

    campaign = (
        db.query(OutreachCampaign)
        .filter(OutreachCampaign.id == message.campaign_id)
        .first()
    )
    target = (
        campaign.destination_url
        if campaign is not None and campaign.destination_url
        else _job_page_url(campaign.job_page_token)
        if campaign is not None and campaign.job_page_token
        else _thanks_url()
    )
    return RedirectResponse(url=target, status_code=302)
