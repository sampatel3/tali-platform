"""Public interest-capture — the outreach CTA landing.

Each outreach email's CTA points at ``/api/v1/public/outreach/interest/{token}``.
A GET marks the message ``interested`` (ratchet, idempotent) and, when the
recipient is a linked prospect, flips ``prospect.status`` to ``interested``, then
302-redirects to the role's public job page (or a minimal FE thanks page when the
campaign has no job page).

GET-with-side-effect is intentional here — this is industry-standard click
tracking. The "effect" is an idempotent flag ratchet, never a destructive write.
No auth (recipients have no account).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...models.outreach_campaign import (
    MESSAGE_STATUS_INTERESTED,
    MESSAGE_STATUS_RANK,
    OutreachCampaign,
    OutreachMessage,
)
from ...models.prospect import PROSPECT_STATUS_INTERESTED, Prospect
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
    # Ratchet: never downgrade a more-advanced or failure state. interested is
    # the strongest positive signal, so it wins over sent/delivered/opened/
    # clicked; failure states (bounced/complained/etc.) are left as-is.
    current_rank = MESSAGE_STATUS_RANK.get(message.status, 0)
    if current_rank <= MESSAGE_STATUS_RANK[MESSAGE_STATUS_INTERESTED]:
        if message.status in MESSAGE_STATUS_RANK:  # only ratchet from tracking states
            message.status = MESSAGE_STATUS_INTERESTED
    message.interested_at = message.interested_at or now

    if message.prospect_id is not None:
        prospect = (
            db.query(Prospect).filter(Prospect.id == message.prospect_id).first()
        )
        if prospect is not None and prospect.status != PROSPECT_STATUS_INTERESTED:
            prospect.status = PROSPECT_STATUS_INTERESTED

    db.commit()

    campaign = (
        db.query(OutreachCampaign)
        .filter(OutreachCampaign.id == message.campaign_id)
        .first()
    )
    target = (
        _job_page_url(campaign.job_page_token)
        if campaign is not None and campaign.job_page_token
        else _thanks_url()
    )
    return RedirectResponse(url=target, status_code=302)
