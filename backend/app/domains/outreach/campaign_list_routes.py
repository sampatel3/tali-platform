"""Paginated recruiter-facing outreach campaign listing."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.outreach_campaign import OutreachCampaign
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from . import campaign_queries as campaign_q
from . import campaign_service as svc

def list_campaigns(
    role_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a bounded page without exposing private role-less campaigns."""

    organization_id = int(current_user.organization_id)
    query = (
        db.query(OutreachCampaign)
        .outerjoin(
            Role,
            and_(
                Role.id == OutreachCampaign.role_id,
                Role.organization_id == organization_id,
            ),
        )
        .filter(OutreachCampaign.organization_id == organization_id)
    )
    if role_id is not None:
        query = query.filter(OutreachCampaign.role_id == role_id)
    roleless_visibility = (
        OutreachCampaign.role_id.is_(None)
        if current_user.role == "owner"
        else and_(
            OutreachCampaign.role_id.is_(None),
            OutreachCampaign.created_by_user_id == current_user.id,
        )
    )
    query = query.filter(
        or_(
            and_(
                OutreachCampaign.role_id.isnot(None),
                Role.id.isnot(None),
                Role.deleted_at.is_(None),
            ),
            roleless_visibility,
        )
    )
    total = int(query.order_by(None).count())
    campaigns = (
        query.order_by(OutreachCampaign.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    counts_by_campaign = campaign_q.compute_counts_bulk(
        db, [int(campaign.id) for campaign in campaigns]
    )
    return {
        "campaigns": [
            svc.serialize_campaign(
                campaign,
                counts=counts_by_campaign.get(int(campaign.id), {}),
            )
            for campaign in campaigns
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
