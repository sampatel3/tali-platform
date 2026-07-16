"""Paginated recruiter-facing outreach campaign listing."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
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
    campaigns, total, counts_by_campaign = campaign_q.campaign_page(
        db,
        organization_id=current_user.organization_id,
        role_id=role_id,
        limit=limit,
        offset=offset,
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
