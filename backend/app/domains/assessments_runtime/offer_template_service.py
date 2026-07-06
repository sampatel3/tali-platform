"""P2: reusable offer templates — org-level default compensation.

CRUD over ``offer_templates``; ``create_offer(template_id=...)`` in
offer_service prefills an offer's comp from one. Org-scoped; flush-not-commit.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.offer import OfferTemplate

_UPDATABLE = frozenset({
    "name",
    "base_salary_amount",
    "currency",
    "pay_frequency",
    "signing_bonus",
    "equity_units",
    "custom_fields",
    "is_active",
})


def list_templates(
    db: Session, organization_id: int, include_inactive: bool = False
) -> list[OfferTemplate]:
    q = db.query(OfferTemplate).filter(
        OfferTemplate.organization_id == organization_id
    )
    if not include_inactive:
        q = q.filter(OfferTemplate.is_active.is_(True))
    return q.order_by(OfferTemplate.name, OfferTemplate.id).all()


def get_template(db: Session, organization_id: int, template_id: int) -> OfferTemplate:
    t = (
        db.query(OfferTemplate)
        .filter(
            OfferTemplate.id == template_id,
            OfferTemplate.organization_id == organization_id,
        )
        .first()
    )
    if t is None:
        raise HTTPException(status_code=404, detail="Offer template not found")
    return t


def create_template(
    db: Session, organization_id: int, *, name: str, **comp
) -> OfferTemplate:
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Template name is required")
    template = OfferTemplate(
        organization_id=organization_id,
        name=clean,
        base_salary_amount=comp.get("base_salary_amount"),
        currency=comp.get("currency"),
        pay_frequency=comp.get("pay_frequency"),
        signing_bonus=comp.get("signing_bonus"),
        equity_units=comp.get("equity_units"),
        custom_fields=comp.get("custom_fields"),
    )
    db.add(template)
    db.flush()
    return template


def update_template(
    db: Session, organization_id: int, template_id: int, updates: dict
) -> OfferTemplate:
    template = get_template(db, organization_id, template_id)
    for key, value in updates.items():
        if key in _UPDATABLE:
            setattr(template, key, value)
    if not (template.name or "").strip():
        raise HTTPException(status_code=422, detail="Template name is required")
    db.flush()
    return template


def delete_template(db: Session, organization_id: int, template_id: int) -> None:
    template = get_template(db, organization_id, template_id)
    db.delete(template)
    db.flush()
