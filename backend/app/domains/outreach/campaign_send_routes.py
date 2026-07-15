"""Campaign send routes and the outbound human-authorization boundary."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_QUEUED,
    OutreachCampaign,
    OutreachMessage,
)
from ...models.user import User
from ...platform.database import get_db
from . import campaign_service as svc

router = APIRouter()
logger = logging.getLogger("taali.outreach.campaigns")


class SendPayload(BaseModel):
    confirm: bool = False


@router.post("/{campaign_id}/send")
def send_campaign(
    campaign_id: int,
    payload: SendPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        current_user.organization_id,
        for_update=payload.confirm,
    )
    if campaign.origin == "agent":
        raise HTTPException(
            status_code=409,
            detail=(
                "Agent-prepared campaigns require campaign-level review via "
                "approve-and-send"
            ),
        )
    if svc.is_archived(campaign):
        raise HTTPException(status_code=409, detail="Campaign is archived")
    if campaign.status == CAMPAIGN_STATUS_SENDING:
        raise HTTPException(status_code=409, detail="Campaign is already sending")
    if campaign.status != CAMPAIGN_STATUS_READY:
        raise HTTPException(status_code=409, detail="Campaign is not ready to send")

    approved = svc.approved_count(db, campaign.id)
    estimate = {"approved_count": approved}
    if not payload.confirm:
        return estimate
    if approved == 0:
        raise HTTPException(status_code=400, detail="No approved messages to send")

    svc.claim_ready_revision(
        db,
        campaign,
        next_status=CAMPAIGN_STATUS_SENDING,
    )

    # Queue only rows that have crossed the explicit approval boundary. The
    # worker selects only queued rows, preventing unapproved delivery.
    db.query(OutreachMessage).filter(
        OutreachMessage.campaign_id == campaign.id,
        OutreachMessage.status == MESSAGE_STATUS_APPROVED,
    ).update(
        {OutreachMessage.status: MESSAGE_STATUS_QUEUED},
        synchronize_session=False,
    )
    db.commit()

    _enqueue_or_restore(db, campaign)
    return {**estimate, "status": CAMPAIGN_STATUS_SENDING}


class ApproveAndSendPayload(BaseModel):
    confirm: bool = False
    expected_will_send_count: Optional[int] = None
    expected_review_token: Optional[str] = None


@router.post("/{campaign_id}/approve-and-send")
def approve_and_send(
    campaign_id: int,
    payload: ApproveAndSendPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Authorize and enqueue an agent-prepared campaign with one HITL action."""
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        current_user.organization_id,
        for_update=payload.confirm,
    )
    if svc.is_archived(campaign):
        raise HTTPException(status_code=409, detail="Campaign is archived")
    if campaign.status == CAMPAIGN_STATUS_SENDING:
        raise HTTPException(status_code=409, detail="Campaign is already sending")
    if campaign.status == CAMPAIGN_STATUS_GENERATING:
        raise HTTPException(status_code=409, detail="Campaign is still generating drafts")
    if campaign.status != CAMPAIGN_STATUS_READY:
        raise HTTPException(status_code=409, detail="Campaign is not ready to send")

    estimate = svc.approve_and_send_estimate(
        db, campaign.id, current_user.organization_id
    )
    if not payload.confirm:
        return estimate
    if campaign.origin == "agent" and not (payload.expected_review_token or "").strip():
        raise HTTPException(
            status_code=428,
            detail="Review the agent-prepared recipients and drafts before approving",
        )
    if (
        payload.expected_will_send_count is not None
        and int(payload.expected_will_send_count) != int(estimate["will_send"])
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Campaign outbound audience changed since it was reviewed; "
                "refresh the send count before approving."
            ),
        )
    if (
        payload.expected_review_token is not None
        and payload.expected_review_token != estimate["review_token"]
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Campaign recipients or drafts changed since they were reviewed; "
                "refresh before approving."
            ),
        )
    if estimate["sendable_count"] == 0:
        raise HTTPException(status_code=400, detail="No drafted messages to send")

    # Claim the exact revision whose digest was validated. PostgreSQL callers
    # also hold the campaign row lock; on SQLite this CAS is the serialization
    # boundary that prevents an edit from landing after validation.
    svc.claim_ready_revision(
        db,
        campaign,
        next_status=CAMPAIGN_STATUS_SENDING,
    )

    db.query(OutreachMessage).filter(
        OutreachMessage.campaign_id == campaign.id,
        OutreachMessage.status == MESSAGE_STATUS_DRAFT,
    ).update(
        {OutreachMessage.status: MESSAGE_STATUS_APPROVED},
        synchronize_session=False,
    )
    db.query(OutreachMessage).filter(
        OutreachMessage.campaign_id == campaign.id,
        OutreachMessage.status == MESSAGE_STATUS_APPROVED,
    ).update(
        {OutreachMessage.status: MESSAGE_STATUS_QUEUED},
        synchronize_session=False,
    )

    # Agents own preparation; an authenticated user owns the consequential
    # outbound authorization until policy grants a different explicit basis.
    campaign.approved_by_user_id = int(current_user.id)
    campaign.approved_at = datetime.now(timezone.utc)
    if campaign.created_by_user_id is None:
        campaign.created_by_user_id = int(current_user.id)
    db.commit()

    _enqueue_or_restore(db, campaign)
    return {**estimate, "status": CAMPAIGN_STATUS_SENDING}


def _enqueue_or_restore(db: Session, campaign: OutreachCampaign) -> None:
    """Enqueue delivery, restoring approved/retryable state on broker failure."""
    from ...tasks.outreach_tasks import send_campaign_messages

    try:
        send_campaign_messages.delay(campaign.id)
    except Exception as exc:  # noqa: BLE001 - compensate durable broker failure
        db.query(OutreachMessage).filter(
            OutreachMessage.campaign_id == campaign.id,
            OutreachMessage.status == MESSAGE_STATUS_QUEUED,
        ).update(
            {OutreachMessage.status: MESSAGE_STATUS_APPROVED},
            synchronize_session=False,
        )
        campaign.status = CAMPAIGN_STATUS_READY
        db.commit()
        logger.exception("Campaign send enqueue failed campaign_id=%s", campaign.id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Outbound send could not be queued; the approved campaign is "
                "safe to retry"
            ),
        ) from exc
