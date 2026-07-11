"""P2: offer lifecycle state machine + approval chain.

create_offer -> draft; transition_offer enforces the allowed status graph and
stamps sent/accepted/declined timestamps. Approvals: sequential groups, each
satisfied when every row in the group is approved; group N cannot be recorded
until group N-1 is complete. offer_is_fully_approved gates the
pending_approval -> approved move AND the draft -> sent short-circuit.
Mutators flush but do NOT commit.
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
    OFFER_TEMPLATE_COMP_FIELDS,
    OFFER_STATUSES,
    Offer,
    OfferApproval,
    OfferTemplate,
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
    template_id: int | None = None,
) -> Offer:
    # Create-from-template: prefill any comp field the caller left unset from the
    # org's template. Explicit args always win; the template is just defaults.
    if template_id is not None:
        template = (
            db.query(OfferTemplate)
            .filter(
                OfferTemplate.id == template_id,
                OfferTemplate.organization_id == organization_id,
            )
            .first()
        )
        if template is None:
            raise HTTPException(status_code=404, detail="Offer template not found")
        _explicit = {
            "base_salary_amount": base_salary_amount,
            "currency": currency,
            "pay_frequency": pay_frequency,
            "signing_bonus": signing_bonus,
            "equity_units": equity_units,
        }
        for field in OFFER_TEMPLATE_COMP_FIELDS:
            if _explicit[field] is None:
                _explicit[field] = getattr(template, field, None)
        base_salary_amount = _explicit["base_salary_amount"]
        currency = _explicit["currency"]
        pay_frequency = _explicit["pay_frequency"]
        signing_bonus = _explicit["signing_bonus"]
        equity_units = _explicit["equity_units"]
        if custom_fields is None and template.custom_fields is not None:
            custom_fields = dict(template.custom_fields)

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
    # Fix (a): a draft may short-circuit straight to sent only when there is no
    # approval chain to satisfy (or it is already fully approved). A draft with
    # pending approvals must route through pending_approval -> approved.
    if target == OFFER_STATUS_SENT and current == OFFER_STATUS_DRAFT:
        if not offer_is_fully_approved(offer):
            raise HTTPException(
                status_code=409,
                detail="Offer has pending approvals — send it through approval before sending it out",
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


def _group_is_complete(offer: Offer, group_order: int) -> bool:
    rows = [a for a in (offer.approvals or []) if a.group_order == group_order]
    if not rows:
        return True
    return all(a.status == "approved" for a in rows)


def record_approval(
    db: Session,
    offer: Offer,
    approval: OfferApproval,
    *,
    acting_user_id: int,
    approved: bool,
) -> OfferApproval:
    """Record one approver's decision on ``approval``.

    Fix (b): only the assigned approver may record their own decision, and only
    while the offer is in pending_approval.
    Fix (c): a decision on group N cannot be recorded until every earlier group
    (N-1, N-2, …) is already fully approved.
    """
    if offer.status != OFFER_STATUS_PENDING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail="Approvals can only be recorded while the offer is awaiting approval",
        )
    if approval.approver_user_id is not None and approval.approver_user_id != acting_user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned approver can record this approval",
        )
    for earlier in sorted({a.group_order for a in (offer.approvals or []) if a.group_order < approval.group_order}):
        if not _group_is_complete(offer, earlier):
            raise HTTPException(
                status_code=409,
                detail="An earlier approval group is still pending — approvals must complete in order",
            )
    approval.status = "approved" if approved else "rejected"
    approval.decided_at = _utcnow()
    db.flush()
    return approval


def offer_is_fully_approved(offer: Offer) -> bool:
    """True when every approval row is approved (and True when the offer has no
    approval rows — no approval required). Fix (c): a group is complete only
    when all of its rows are approved, so a partially-approved group blocks."""
    approvals = list(offer.approvals or [])
    if not approvals:
        return True
    by_group: dict[int, list[OfferApproval]] = defaultdict(list)
    for a in approvals:
        by_group[a.group_order].append(a)
    for group in by_group.values():
        if not all(a.status == "approved" for a in group):
            return False
    return True
