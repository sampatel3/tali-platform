"""P2: offer-template management API. Reads and writes are open to any
authenticated org member."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from .offer_template_service import (
    create_template,
    delete_template,
    list_templates,
    update_template,
)

router = APIRouter(tags=["Offer Templates"])


class OfferTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_salary_amount: int | None = None
    currency: str | None = None
    pay_frequency: str | None = None
    signing_bonus: int | None = None
    equity_units: int | None = None
    custom_fields: dict | None = None
    is_active: bool = True


class OfferTemplateCreate(BaseModel):
    name: str
    base_salary_amount: int | None = None
    currency: str | None = None
    pay_frequency: str | None = None
    signing_bonus: int | None = None
    equity_units: int | None = None
    custom_fields: dict | None = None


class OfferTemplateUpdate(BaseModel):
    name: str | None = None
    base_salary_amount: int | None = None
    currency: str | None = None
    pay_frequency: str | None = None
    signing_bonus: int | None = None
    equity_units: int | None = None
    custom_fields: dict | None = None
    is_active: bool | None = None


@router.get("/offer-templates", response_model=list[OfferTemplateOut])
def get_offer_templates(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return list_templates(
        db, current_user.organization_id, include_inactive=include_inactive
    )


@router.post("/offer-templates", response_model=OfferTemplateOut, status_code=201)
def create_offer_template(
    data: OfferTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = create_template(
        db, current_user.organization_id, name=data.name,
        **data.model_dump(exclude={"name"}),
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/offer-templates/{template_id}", response_model=OfferTemplateOut)
def patch_offer_template(
    template_id: int,
    data: OfferTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = update_template(
        db, current_user.organization_id, template_id,
        data.model_dump(exclude_unset=True),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/offer-templates/{template_id}", status_code=204)
def remove_offer_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delete_template(db, current_user.organization_id, template_id)
    db.commit()
    return None
