"""Cross-database serialization for reviewed outreach campaigns."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_READY,
    OutreachCampaign,
)


def _owned_campaign_query(
    db: Session,
    campaign_id: int,
    org_id: int,
    *,
    for_update: bool = False,
):
    query = db.query(OutreachCampaign).filter(
        OutreachCampaign.id == campaign_id,
        OutreachCampaign.organization_id == org_id,
    )
    # PostgreSQL serializes all ready-snapshot mutations on this row. SQLite
    # intentionally ignores FOR UPDATE, so ``claim_ready_revision`` below is
    # also mandatory and supplies the cross-database compare-and-set fence.
    return query.with_for_update() if for_update else query


def get_owned_campaign(
    db: Session,
    campaign_id: int,
    org_id: int,
    *,
    for_update: bool = False,
) -> OutreachCampaign:
    from fastapi import HTTPException

    campaign = _owned_campaign_query(
        db,
        campaign_id,
        org_id,
        for_update=for_update,
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def claim_ready_revision(
    db: Session,
    campaign: OutreachCampaign,
    *,
    next_status: Optional[str] = None,
) -> int:
    """Atomically fence one mutation of a ready campaign.

    Callers must claim before changing any message. A concurrent edit/send can
    therefore win the revision exactly once; every stale contender rolls back
    before touching the reviewed snapshot.
    """
    from fastapi import HTTPException

    expected_revision = int(campaign.review_revision or 0)
    values = {OutreachCampaign.review_revision: expected_revision + 1}
    if next_status is not None:
        values[OutreachCampaign.status] = next_status
    claimed = (
        db.query(OutreachCampaign)
        .filter(
            OutreachCampaign.id == campaign.id,
            OutreachCampaign.organization_id == campaign.organization_id,
            OutreachCampaign.status == CAMPAIGN_STATUS_READY,
            OutreachCampaign.review_revision == expected_revision,
        )
        .update(values, synchronize_session=False)
    )
    if claimed != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Campaign changed while this request was in progress; "
                "refresh and retry"
            ),
        )
    return expected_revision + 1
