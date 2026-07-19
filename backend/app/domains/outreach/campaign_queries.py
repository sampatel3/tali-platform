"""Read-only campaign pagination and message rollup queries.

Keeping these query shapes together makes route pagination deterministic and
lets list/detail endpoints share the same exact count aggregation.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.outreach_campaign import OutreachCampaign, OutreachMessage


def _counts_from_statuses(by_status: dict[str, int]) -> dict[str, int]:
    audience = sum(by_status.values())
    drafted = sum(
        count
        for status, count in by_status.items()
        if status not in ("pending", "drafting")
    )
    return {
        "audience": audience,
        "drafted": drafted,
        # Exact actionable states sit alongside the lifecycle rollups above.
        # Consumers must not infer current drafts from ``drafted`` because that
        # rollup intentionally also includes approved and delivery outcomes.
        "pending": by_status.get("pending", 0),
        "draft": by_status.get("draft", 0),
        "approved": by_status.get("approved", 0),
        "sent": by_status.get("sent", 0)
        + by_status.get("delivered", 0)
        + by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "delivered": by_status.get("delivered", 0)
        + by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "opened": by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "clicked": by_status.get("clicked", 0) + by_status.get("interested", 0),
        "interested": by_status.get("interested", 0),
        "bounced": by_status.get("bounced", 0),
        "failed": by_status.get("failed", 0),
    }


def compute_counts(db: Session, campaign_id: int) -> dict[str, int]:
    """Return the exact message-state rollup for one campaign."""
    rows = (
        db.query(OutreachMessage.status, sa_func.count(OutreachMessage.id))
        .filter(OutreachMessage.campaign_id == campaign_id)
        .group_by(OutreachMessage.status)
        .all()
    )
    return _counts_from_statuses({status: int(count) for status, count in rows})


def compute_counts_bulk(
    db: Session,
    campaign_ids: list[int],
) -> dict[int, dict[str, int]]:
    """Return exact message rollups for a campaign page in one query."""
    ids = sorted({int(campaign_id) for campaign_id in campaign_ids})
    if not ids:
        return {}
    rows = (
        db.query(
            OutreachMessage.campaign_id,
            OutreachMessage.status,
            sa_func.count(OutreachMessage.id),
        )
        .filter(OutreachMessage.campaign_id.in_(ids))
        .group_by(OutreachMessage.campaign_id, OutreachMessage.status)
        .all()
    )
    statuses: dict[int, dict[str, int]] = {campaign_id: {} for campaign_id in ids}
    for campaign_id, status, count in rows:
        statuses[int(campaign_id)][str(status)] = int(count)
    return {
        campaign_id: _counts_from_statuses(by_status)
        for campaign_id, by_status in statuses.items()
    }


def campaign_page(
    db: Session,
    *,
    organization_id: int,
    role_id: Optional[int],
    limit: int,
    offset: int,
) -> tuple[list[OutreachCampaign], int, dict[int, dict[str, int]]]:
    """Return a stable newest-first campaign page and its exact rollups."""
    query = db.query(OutreachCampaign).filter(
        OutreachCampaign.organization_id == organization_id
    )
    if role_id is not None:
        query = query.filter(OutreachCampaign.role_id == role_id)
    total = query.order_by(None).count()
    campaigns = (
        query.order_by(OutreachCampaign.id.desc()).offset(offset).limit(limit).all()
    )
    counts = compute_counts_bulk(db, [int(campaign.id) for campaign in campaigns])
    return campaigns, total, counts


def message_page(
    db: Session,
    *,
    campaign_id: int,
    limit: int,
    offset: int,
) -> tuple[list[OutreachMessage], dict[str, int]]:
    """Return a stable oldest-first message page plus full campaign counts."""
    counts = compute_counts(db, campaign_id)
    messages = (
        db.query(OutreachMessage)
        .filter(OutreachMessage.campaign_id == campaign_id)
        .order_by(OutreachMessage.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return messages, counts
