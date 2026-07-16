"""Best-effort immediate publish for durable campaign work."""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_ARCHIVED,
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    CAMPAIGN_STATUS_SENT,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_QUEUED,
    OutreachCampaign,
    OutreachMessage,
)

logger = logging.getLogger("taali.outreach.dispatch")


def kick_campaign_work(campaign_id: int, *, operation: str) -> tuple[bool, str | None]:
    """Publish a fast-path kick; committed campaign/message state is recovery.

    A broker exception is ambiguous (the broker may have accepted the task), so
    callers report ``dispatch_pending`` without rolling back the durable state.
    Duplicate delivery is safe because workers claim individual message rows.
    """
    try:
        if operation == "generate":
            from ...tasks.outreach_tasks import generate_campaign_drafts as task
        elif operation == "send":
            from ...tasks.outreach_tasks import send_campaign_messages as task
        else:  # pragma: no cover - internal programming error
            raise ValueError(f"unsupported campaign operation: {operation}")
        result = task.delay(int(campaign_id))
        return True, str(result.id) if getattr(result, "id", None) else None
    except Exception:
        logger.exception(
            "campaign broker publish ambiguous/failed campaign=%s operation=%s; "
            "durable recovery will retry",
            campaign_id,
            operation,
        )
        return False, None


def claim_campaign_send(
    db: Session,
    *,
    campaign_id: int,
    organization_id: int,
) -> int:
    """Atomically authorize a stable campaign state and queue approved rows.

    Archive locks the same campaign row. Whichever transition commits first is
    authoritative, so a request holding a stale READY ORM object can never
    overwrite a completed archive with SENDING.
    """

    claimed = (
        db.query(OutreachCampaign)
        .filter(
            OutreachCampaign.id == int(campaign_id),
            OutreachCampaign.organization_id == int(organization_id),
            OutreachCampaign.status.in_(
                [
                    CAMPAIGN_STATUS_DRAFT,
                    CAMPAIGN_STATUS_READY,
                    CAMPAIGN_STATUS_SENT,
                ]
            ),
        )
        .update(
            {OutreachCampaign.status: CAMPAIGN_STATUS_SENDING},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        db.rollback()
        current_status = (
            db.query(OutreachCampaign.status)
            .filter(
                OutreachCampaign.id == int(campaign_id),
                OutreachCampaign.organization_id == int(organization_id),
            )
            .scalar()
        )
        messages = {
            CAMPAIGN_STATUS_ARCHIVED: "Campaign is archived",
            CAMPAIGN_STATUS_SENDING: "Campaign is already sending",
            CAMPAIGN_STATUS_GENERATING: "Campaign is still generating drafts",
        }
        raise HTTPException(
            status_code=409,
            detail=messages.get(current_status, "Campaign is not ready to send"),
        )

    queued = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == int(campaign_id),
            OutreachMessage.status == MESSAGE_STATUS_APPROVED,
        )
        .update(
            {OutreachMessage.status: MESSAGE_STATUS_QUEUED},
            synchronize_session=False,
        )
    )
    if queued <= 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="No approved messages remain to send",
        )
    db.commit()
    return int(queued)
