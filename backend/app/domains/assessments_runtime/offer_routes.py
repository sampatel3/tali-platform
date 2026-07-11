"""P2: offer lifecycle API + HRIS export.

Exposes the offer state machine (built in ``offer_service``) over HTTP. Reads
and writes are open to any authenticated org member. Everything is org-scoped —
an offer is only reachable through its own organization.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.candidate_application import CandidateApplication
from ...models.offer import Offer
from ...models.user import User
from ...platform.database import get_db
from .offer_esign_service import build_esign_request
from .offer_hris_service import build_hris_payload
from .offer_service import (
    add_approval,
    create_offer,
    record_approval,
    transition_offer,
)

router = APIRouter(tags=["Offers"])


class OfferApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_order: int
    group_quorum: int
    approver_user_id: int | None = None
    status: str


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    version: int
    status: str
    base_salary_amount: int | None = None
    currency: str | None = None
    pay_frequency: str | None = None
    signing_bonus: int | None = None
    equity_units: int | None = None
    custom_fields: dict | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    approvals: list[OfferApprovalOut] = []


class OfferCreate(BaseModel):
    base_salary_amount: int | None = None
    currency: str | None = None
    pay_frequency: str | None = None
    signing_bonus: int | None = None
    equity_units: int | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    custom_fields: dict | None = None
    template_id: int | None = None


class OfferTransition(BaseModel):
    status: str


class ApprovalCreate(BaseModel):
    group_order: int = 0
    group_quorum: int = 1
    approver_user_id: int | None = None


class ApprovalRecord(BaseModel):
    approved: bool


def _get_application(db: Session, org_id: int, application_id: int) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _get_offer(db: Session, org_id: int, offer_id: int) -> Offer:
    offer = (
        db.query(Offer)
        .filter(Offer.id == offer_id, Offer.organization_id == org_id)
        .first()
    )
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


@router.get("/applications/{application_id}/offers", response_model=list[OfferOut])
def list_application_offers(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_application(db, current_user.organization_id, application_id)
    return (
        db.query(Offer)
        .filter(
            Offer.organization_id == current_user.organization_id,
            Offer.application_id == application_id,
        )
        .order_by(Offer.version.desc())
        .all()
    )


@router.post(
    "/applications/{application_id}/offers", response_model=OfferOut, status_code=201
)
def create_application_offer(
    application_id: int,
    data: OfferCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_application(db, current_user.organization_id, application_id)
    offer = create_offer(
        db,
        organization_id=current_user.organization_id,
        application_id=application_id,
        created_by_user_id=current_user.id,
        **data.model_dump(),
    )
    db.commit()
    db.refresh(offer)
    return offer


@router.get("/offers/{offer_id}", response_model=OfferOut)
def get_offer(
    offer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_offer(db, current_user.organization_id, offer_id)


@router.post("/offers/{offer_id}/transition", response_model=OfferOut)
def post_offer_transition(
    offer_id: int,
    data: OfferTransition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    offer = _get_offer(db, current_user.organization_id, offer_id)
    transition_offer(db, offer, data.status)
    db.commit()
    db.refresh(offer)
    return offer


@router.post("/offers/{offer_id}/approvals", response_model=OfferApprovalOut, status_code=201)
def post_offer_approval(
    offer_id: int,
    data: ApprovalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    offer = _get_offer(db, current_user.organization_id, offer_id)
    approval = add_approval(
        db, offer,
        group_order=data.group_order,
        group_quorum=data.group_quorum,
        approver_user_id=data.approver_user_id,
    )
    db.commit()
    db.refresh(approval)
    return approval


@router.post(
    "/offers/{offer_id}/approvals/{approval_id}/record",
    response_model=OfferApprovalOut,
)
def post_record_approval(
    offer_id: int,
    approval_id: int,
    data: ApprovalRecord,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    offer = _get_offer(db, current_user.organization_id, offer_id)
    approval = next((a for a in offer.approvals if a.id == approval_id), None)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    record_approval(
        db, offer, approval, acting_user_id=current_user.id, approved=data.approved
    )
    db.commit()
    db.refresh(approval)
    return approval


@router.get("/offers/{offer_id}/hris-export")
def get_offer_hris_export(
    offer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The vendor-neutral HRIS import payload for an offer (see offer_hris_service)."""
    offer = _get_offer(db, current_user.organization_id, offer_id)
    return build_hris_payload(offer)


@router.get("/offers/{offer_id}/esign-request")
def get_offer_esign_request(
    offer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The vendor-neutral e-sign signature-request payload (see offer_esign_service)."""
    offer = _get_offer(db, current_user.organization_id, offer_id)
    return build_esign_request(offer)
