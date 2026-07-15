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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_ARCHIVED,
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from . import campaign_service as svc
from .campaign_send_routes import router as campaign_send_router

router = APIRouter(prefix="/outreach/campaigns", tags=["Outreach campaigns"])


def _require_campaign_status(
    campaign: OutreachCampaign,
    expected: str,
    *,
    action: str,
) -> None:
    if campaign.status != expected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Campaign must be {expected} to {action}; "
                f"current status is {campaign.status}"
            ),
        )


# --------------------------------------------------------------------------- #
# Create / list / detail / patch / archive
# --------------------------------------------------------------------------- #


class CreateCampaignPayload(BaseModel):
    name: str
    role_id: int


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

    role = (
        db.query(Role)
        .filter(Role.id == payload.role_id, Role.organization_id == org_id)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    destination = svc.resolve_campaign_destination(db, role.id)
    campaign = OutreachCampaign(
        organization_id=org_id,
        role_id=role.id,
        name=name,
        brief=svc.default_brief(role.name, role.job_spec_text),
        job_page_token=destination["job_page_token"],
        destination_url=destination["destination_url"],
        destination_provider=destination["provider"],
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
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        current_user.organization_id,
        for_update=True,
    )
    if campaign.status == CAMPAIGN_STATUS_GENERATING:
        raise HTTPException(
            status_code=409,
            detail="Campaign cannot be changed while drafts are generating",
        )
    name = campaign.name
    brief = campaign.brief
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
    if payload.brief is not None:
        brief = payload.brief
    changed = name != campaign.name or brief != campaign.brief
    if changed and campaign.status == CAMPAIGN_STATUS_READY:
        svc.claim_ready_revision(db, campaign)
    campaign.name = name
    campaign.brief = brief
    db.commit()
    db.refresh(campaign)
    return svc.serialize_campaign(campaign, counts=svc.compute_counts(db, campaign.id))


@router.post("/{campaign_id}/archive")
def archive_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        current_user.organization_id,
        for_update=True,
    )
    if campaign.status in {CAMPAIGN_STATUS_GENERATING, CAMPAIGN_STATUS_SENDING}:
        raise HTTPException(
            status_code=409,
            detail=f"Campaign cannot be archived while {campaign.status}",
        )
    if campaign.status == CAMPAIGN_STATUS_READY:
        svc.claim_ready_revision(
            db,
            campaign,
            next_status=CAMPAIGN_STATUS_ARCHIVED,
        )
    else:
        campaign.status = CAMPAIGN_STATUS_ARCHIVED
    db.commit()
    return svc.serialize_campaign(campaign, counts=svc.compute_counts(db, campaign.id))


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #


class AudiencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_ids: list[int] = Field(default_factory=list)


@router.post("/{campaign_id}/audience")
def add_audience(
    campaign_id: int,
    payload: AudiencePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = svc.get_owned_campaign(db, campaign_id, current_user.organization_id)
    _require_campaign_status(campaign, CAMPAIGN_STATUS_DRAFT, action="change its audience")
    result = svc.resolve_audience(
        db,
        campaign=campaign,
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
    if campaign.status == CAMPAIGN_STATUS_GENERATING:
        raise HTTPException(status_code=409, detail="Campaign is already generating")
    _require_campaign_status(campaign, CAMPAIGN_STATUS_DRAFT, action="generate drafts")

    # Re-resolve at the paid-work boundary rather than trusting the snapshot
    # captured at campaign creation.  This lets a newly published native page
    # or newly synced ATS apply URL unblock an existing draft campaign, while
    # ensuring generated outreach never leads only to a generic click page.
    destination = svc.resolve_campaign_destination(db, campaign.role_id)
    if destination.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail="application_destination_required",
        )
    campaign.job_page_token = destination.get("job_page_token")
    campaign.destination_url = destination.get("destination_url")
    campaign.destination_provider = destination.get("provider")
    db.commit()

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

    # Claim the campaign with a conditional update. The early status check gives
    # sequential callers a clear conflict response; this compare-and-set also
    # closes the window where two concurrent confirms both read the campaign
    # before either transaction commits and would otherwise enqueue two tasks.
    claimed = (
        db.query(OutreachCampaign)
        .filter(
            OutreachCampaign.id == campaign.id,
            OutreachCampaign.organization_id == current_user.organization_id,
            OutreachCampaign.status == CAMPAIGN_STATUS_DRAFT,
        )
        .update(
            {OutreachCampaign.status: CAMPAIGN_STATUS_GENERATING},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Campaign is already generating")
    db.commit()

    from ...tasks.outreach_tasks import generate_campaign_drafts

    try:
        generate_campaign_drafts.delay(campaign.id)
    except Exception as exc:
        # Restore the only legal pre-generation state. The audience is already
        # durable, so the same request can be retried without rebuilding it.
        restored = db.get(OutreachCampaign, int(campaign.id))
        if restored is not None and restored.status == CAMPAIGN_STATUS_GENERATING:
            restored.status = CAMPAIGN_STATUS_DRAFT
            db.commit()
        raise HTTPException(
            status_code=503,
            detail="Draft generation could not be queued; the campaign is safe to retry",
        ) from exc
    return {**estimate, "status": CAMPAIGN_STATUS_GENERATING}


# --------------------------------------------------------------------------- #
# Message edit / approve / reject
# --------------------------------------------------------------------------- #


def _get_owned_message(
    db: Session, campaign_id: int, mid: int, org_id: int
) -> tuple[OutreachCampaign, OutreachMessage]:
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        org_id,
        for_update=True,
    )
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
    return campaign, message


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
    campaign = svc.get_owned_campaign(
        db,
        campaign_id,
        org_id,
        for_update=True,
    )
    _require_campaign_status(campaign, CAMPAIGN_STATUS_READY, action="approve messages")
    if campaign.origin == "agent":
        raise HTTPException(
            status_code=409,
            detail=(
                "Agent-prepared campaigns require campaign-level review via "
                "approve-and-send"
            ),
        )
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
    if not rows:
        return {"approved": 0}
    svc.claim_ready_revision(db, campaign)
    for m in rows:
        m.status = MESSAGE_STATUS_APPROVED
    campaign.counts = svc.compute_counts(db, campaign.id)
    db.commit()
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
    campaign, message = _get_owned_message(
        db, campaign_id, mid, current_user.organization_id
    )
    _require_campaign_status(campaign, CAMPAIGN_STATUS_READY, action="edit messages")
    if message.status not in (MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_APPROVED):
        raise HTTPException(
            status_code=409,
            detail="Only draft or approved messages can be edited",
        )
    has_edit = payload.subject is not None or payload.body is not None
    if not has_edit:
        return svc.serialize_message(message)
    svc.claim_ready_revision(db, campaign)
    if payload.subject is not None:
        message.subject = payload.subject
    if payload.body is not None:
        message.body = payload.body
    # Content approval applies to an exact snapshot. Any subsequent edit,
    # including an identical-value submission, must cross review again.
    if message.status == MESSAGE_STATUS_APPROVED:
        message.status = MESSAGE_STATUS_DRAFT
    campaign.counts = svc.compute_counts(db, campaign.id)
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
    campaign, message = _get_owned_message(
        db, campaign_id, mid, current_user.organization_id
    )
    _require_campaign_status(campaign, CAMPAIGN_STATUS_READY, action="reject messages")
    # Only pre-send states can be rejected — resurrecting a sent/delivered row
    # would allow a second send to the same recipient and corrupt tracking.
    if message.status not in (MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_APPROVED):
        raise HTTPException(
            status_code=409,
            detail="Only draft or approved messages can be rejected",
        )
    svc.claim_ready_revision(db, campaign)
    # Back to pending — excluded from send.
    message.status = MESSAGE_STATUS_PENDING
    campaign.counts = svc.compute_counts(db, campaign.id)
    db.commit()
    db.refresh(message)
    return svc.serialize_message(message)


# Keep the send/HITL routes under the same public domain router.
router.include_router(campaign_send_router)
