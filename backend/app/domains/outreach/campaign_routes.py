"""Outreach campaign routes — draft, audience, generate, approve, send, track.

Recruiter-auth, org-scoped. The heavy lifting (audience rails, serialization,
rollups) lives in ``campaign_service``; the two-phase LLM/send confirm mirrors
PoolRescore (POST returns an estimate; POST with ``confirm=true`` enqueues the
Celery task). The approval gate is absolute — the send route enqueues, and the
send TASK re-checks that only ``approved`` rows go out.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_ARCHIVED,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_PENDING,
    MESSAGE_STATUS_QUEUED,
    OutreachCampaign,
    OutreachMessage,
)
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from . import campaign_service as svc

router = APIRouter(prefix="/outreach/campaigns", tags=["Outreach campaigns"])


# --------------------------------------------------------------------------- #
# Create / list / detail / patch / archive
# --------------------------------------------------------------------------- #


class CreateCampaignPayload(BaseModel):
    name: str
    role_id: Optional[int] = None


@router.post("")
def create_campaign(
    payload: CreateCampaignPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    role: Optional[Role] = None
    if payload.role_id is not None:
        role = (
            db.query(Role)
            .filter(Role.id == payload.role_id, Role.organization_id == org_id)
            .first()
        )
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")

    campaign = OutreachCampaign(
        organization_id=org_id,
        role_id=role.id if role else None,
        name=name,
        brief=svc.default_brief(
            role.name if role else None, role.job_spec_text if role else None
        ),
        job_page_token=svc.resolve_job_page_token(db, role.id if role else None),
        created_by_user_id=current_user.id,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return svc.serialize_campaign(campaign, counts={})


@router.get("")
def list_campaigns(
    role_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    query = db.query(OutreachCampaign).filter(
        OutreachCampaign.organization_id == org_id
    )
    if role_id is not None:
        query = query.filter(OutreachCampaign.role_id == role_id)
    campaigns = query.order_by(OutreachCampaign.id.desc()).all()
    return {
        "campaigns": [
            svc.serialize_campaign(c, counts=svc.compute_counts(db, c.id))
            for c in campaigns
        ]
    }


@router.get("/{campaign_id}")
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    messages = (
        db.query(OutreachMessage)
        .filter(OutreachMessage.campaign_id == campaign.id)
        .order_by(OutreachMessage.id.asc())
        .all()
    )
    return svc.serialize_campaign(
        campaign, counts=svc.compute_counts(db, campaign.id), messages=messages
    )


class PatchCampaignPayload(BaseModel):
    name: Optional[str] = None
    brief: Optional[str] = None


@router.patch("/{campaign_id}")
def patch_campaign(
    campaign_id: int,
    payload: PatchCampaignPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        campaign.name = name
    if payload.brief is not None:
        campaign.brief = payload.brief
    db.commit()
    db.refresh(campaign)
    return svc.serialize_campaign(campaign, counts=svc.compute_counts(db, campaign.id))


@router.post("/{campaign_id}/archive")
def archive_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    campaign.status = CAMPAIGN_STATUS_ARCHIVED
    db.commit()
    return svc.serialize_campaign(campaign, counts=svc.compute_counts(db, campaign.id))


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #


class AudiencePayload(BaseModel):
    prospect_ids: list[int] = []
    application_ids: list[int] = []


@router.post("/{campaign_id}/audience")
def add_audience(
    campaign_id: int,
    payload: AudiencePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    if svc.is_archived(campaign):
        raise HTTPException(status_code=409, detail="Campaign is archived")
    result = svc.resolve_audience(
        db,
        campaign=campaign,
        prospect_ids=payload.prospect_ids or [],
        application_ids=payload.application_ids or [],
    )
    svc.refresh_counts(db, campaign)
    return result


# --------------------------------------------------------------------------- #
# Generate (two-phase confirm, like PoolRescore)
# --------------------------------------------------------------------------- #


class GeneratePayload(BaseModel):
    confirm: bool = False


@router.post("/{campaign_id}/generate")
def generate_drafts(
    campaign_id: int,
    payload: GeneratePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    if svc.is_archived(campaign):
        raise HTTPException(status_code=409, detail="Campaign is archived")

    pending = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == campaign.id,
            OutreachMessage.status == MESSAGE_STATUS_PENDING,
        )
        .count()
    )
    estimate = {
        "count": pending,
        "estimated_cost_usd": round(pending * svc.COST_PER_DRAFT_USD, 2),
    }
    if not payload.confirm:
        return estimate
    if pending == 0:
        raise HTTPException(status_code=400, detail="No pending messages to draft")

    campaign.status = CAMPAIGN_STATUS_GENERATING
    db.commit()

    from ...tasks.outreach_tasks import generate_campaign_drafts

    generate_campaign_drafts.delay(campaign.id)
    return {**estimate, "status": campaign.status}


# --------------------------------------------------------------------------- #
# Message edit / approve / reject
# --------------------------------------------------------------------------- #


def _get_owned_message(
    db: Session, campaign_id: int, mid: int, org_id: int
) -> OutreachMessage:
    svc.get_owned_campaign(db, campaign_id, org_id)  # 404s if campaign not owned
    message = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.id == mid,
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.organization_id == org_id,
        )
        .first()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


class ApprovePayload(BaseModel):
    message_ids: Optional[list[int]] = None
    all_drafts: bool = False


# NOTE: this static-suffix route is declared BEFORE the ``/{mid}`` edit route
# below so FastAPI matches ``/messages/approve`` here instead of parsing
# "approve" as ``mid``.
@router.post("/{campaign_id}/messages/approve")
def approve_messages(
    campaign_id: int,
    payload: ApprovePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    campaign = svc.get_owned_campaign(db, campaign_id, org_id)
    if payload.all_drafts:
        ids = svc.approvable_draft_ids(db, campaign.id)
    else:
        ids = list(payload.message_ids or [])
    if not ids:
        return {"approved": 0}

    rows = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.id.in_(ids),
            OutreachMessage.campaign_id == campaign.id,
            OutreachMessage.organization_id == org_id,
            OutreachMessage.status == MESSAGE_STATUS_DRAFT,
        )
        .all()
    )
    for m in rows:
        m.status = MESSAGE_STATUS_APPROVED
    db.commit()
    svc.refresh_counts(db, campaign)
    return {"approved": len(rows)}


class EditMessagePayload(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


@router.post("/{campaign_id}/messages/{mid}")
def edit_message(
    campaign_id: int,
    mid: int,
    payload: EditMessagePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message = _get_owned_message(db, campaign_id, mid, current_user.organization_id)
    if message.status not in (MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_APPROVED):
        raise HTTPException(
            status_code=409,
            detail="Only draft or approved messages can be edited",
        )
    if payload.subject is not None:
        message.subject = payload.subject
    if payload.body is not None:
        message.body = payload.body
    db.commit()
    db.refresh(message)
    return svc.serialize_message(message)


@router.post("/{campaign_id}/messages/{mid}/reject")
def reject_message(
    campaign_id: int,
    mid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message = _get_owned_message(db, campaign_id, mid, current_user.organization_id)
    # Only pre-send states can be rejected — resurrecting a sent/delivered row
    # would allow a second send to the same recipient and corrupt tracking.
    if message.status not in (MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_APPROVED):
        raise HTTPException(status_code=409, detail="Only draft or approved messages can be rejected")
    # Back to pending — excluded from send.
    message.status = MESSAGE_STATUS_PENDING
    db.commit()
    db.refresh(message)
    return svc.serialize_message(message)


# --------------------------------------------------------------------------- #
# Send (two-phase confirm)
# --------------------------------------------------------------------------- #


class SendPayload(BaseModel):
    confirm: bool = False


@router.post("/{campaign_id}/send")
def send_campaign(
    campaign_id: int,
    payload: SendPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    if svc.is_archived(campaign):
        raise HTTPException(status_code=409, detail="Campaign is archived")
    if campaign.status == CAMPAIGN_STATUS_SENDING:
        raise HTTPException(status_code=409, detail="Campaign is already sending")

    approved = svc.approved_count(db, campaign.id)
    estimate = {"approved_count": approved}
    if not payload.confirm:
        return estimate
    if approved == 0:
        raise HTTPException(status_code=400, detail="No approved messages to send")

    # Atomically move approved -> queued in the same transaction that flips
    # the campaign to sending: a racing second confirm finds zero approved
    # rows (and a 409), so two workers can never double-select a message.
    # 'queued' is reachable ONLY from 'approved' here — the send task selects
    # queued, preserving the nothing-sends-unapproved invariant.
    db.query(OutreachMessage).filter(
        OutreachMessage.campaign_id == campaign.id,
        OutreachMessage.status == MESSAGE_STATUS_APPROVED,
    ).update({OutreachMessage.status: MESSAGE_STATUS_QUEUED}, synchronize_session=False)
    campaign.status = CAMPAIGN_STATUS_SENDING
    db.commit()

    from ...tasks.outreach_tasks import send_campaign_messages

    send_campaign_messages.delay(campaign.id)
    return {**estimate, "status": campaign.status}
