"""P2: offer lifecycle state machine + approval chain.

create_offer -> draft; transition_offer enforces the allowed status graph and
stamps sent/accepted/declined timestamps. Approvals: sequential groups, each
satisfied when ``group_quorum`` members approve; offer_is_fully_approved gates the
pending_approval -> approved move. Mutators flush but do NOT commit.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.offer import (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_APPROVED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_DEPRECATED,
    OFFER_STATUS_DRAFT,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_PENDING_APPROVAL,
    OFFER_STATUS_SENT,
    OFFER_STATUSES,
    Offer,
    OfferApproval,
)

_ALLOWED_TRANSITIONS = {
    OFFER_STATUS_DRAFT: {OFFER_STATUS_PENDING_APPROVAL, OFFER_STATUS_SENT, OFFER_STATUS_DEPRECATED},
    OFFER_STATUS_PENDING_APPROVAL: {OFFER_STATUS_APPROVED, OFFER_STATUS_DRAFT, OFFER_STATUS_DEPRECATED},
    OFFER_STATUS_APPROVED: {OFFER_STATUS_SENT, OFFER_STATUS_DEPRECATED},
    OFFER_STATUS_SENT: {OFFER_STATUS_ACCEPTED, OFFER_STATUS_DECLINED, OFFER_STATUS_EXPIRED, OFFER_STATUS_DEPRECATED},
    OFFER_STATUS_EXPIRED: {OFFER_STATUS_SENT, OFFER_STATUS_DEPRECATED},
    OFFER_STATUS_ACCEPTED: set(),
    OFFER_STATUS_DECLINED: set(),
    OFFER_STATUS_DEPRECATED: set(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_offer(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    created_by_user_id: int | None = None,
    base_salary_amount: int | None = None,
    currency: str | None = None,
    pay_frequency: str | None = None,
    signing_bonus: int | None = None,
    equity_units: int | None = None,
    starts_at: datetime | None = None,
    expires_at: datetime | None = None,
    custom_fields: dict | None = None,
) -> Offer:
    current_max = (
        db.query(sa_func.max(Offer.version))
        .filter(Offer.application_id == application_id)
        .scalar()
    )
    offer = Offer(
        organization_id=organization_id,
        application_id=application_id,
        version=int(current_max or 0) + 1,
        status=OFFER_STATUS_DRAFT,
        base_salary_amount=base_salary_amount,
        currency=currency,
        pay_frequency=pay_frequency,
        signing_bonus=signing_bonus,
        equity_units=equity_units,
        starts_at=starts_at,
        expires_at=expires_at,
        custom_fields=custom_fields,
        created_by_user_id=created_by_user_id,
    )
    db.add(offer)
    db.flush()
    return offer


def transition_offer(db: Session, offer: Offer, to_status: str) -> Offer:
    target = (to_status or "").strip().lower()
    if target not in OFFER_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unsupported offer status={to_status!r}")
    current = offer.status
    if target == current:
        return offer
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409, detail=f"Offer transition {current}->{target} is not allowed"
        )
    if target == OFFER_STATUS_APPROVED and not offer_is_fully_approved(offer):
        raise HTTPException(
            status_code=409, detail="Offer cannot be approved until all approval groups meet quorum"
        )
    now = _utcnow()
    offer.status = target
    if target == OFFER_STATUS_SENT:
        offer.sent_at = now
    elif target == OFFER_STATUS_ACCEPTED:
        offer.accepted_at = now
    elif target == OFFER_STATUS_DECLINED:
        offer.declined_at = now
    db.flush()
    return offer


def add_approval(
    db: Session,
    offer: Offer,
    *,
    group_order: int = 0,
    group_quorum: int = 1,
    approver_user_id: int | None = None,
) -> OfferApproval:
    approval = OfferApproval(
        group_order=group_order,
        group_quorum=group_quorum,
        approver_user_id=approver_user_id,
        status="pending",
    )
    offer.approvals.append(approval)  # keeps the in-memory collection consistent
    db.flush()
    return approval


def record_approval(
    db: Session, approval: OfferApproval, *, approved: bool
) -> OfferApproval:
    approval.status = "approved" if approved else "rejected"
    approval.decided_at = _utcnow()
    db.flush()
    return approval


def offer_is_fully_approved(offer: Offer) -> bool:
    """True when every approval group has met its quorum (and True when the offer
    has no approval rows — no approval required)."""
    approvals = list(offer.approvals or [])
    if not approvals:
        return True
    by_group: dict[int, list[OfferApproval]] = defaultdict(list)
    for a in approvals:
        by_group[a.group_order].append(a)
    for group in by_group.values():
        quorum = max((a.group_quorum for a in group), default=1)
        approved = sum(1 for a in group if a.status == "approved")
        if approved < quorum:
            return False
    return True
