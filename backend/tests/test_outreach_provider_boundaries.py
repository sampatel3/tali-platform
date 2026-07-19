"""Outreach workers release SQL transactions before paid/provider work."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.outreach_campaign import (
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_SENDING,
    MESSAGE_STATUS_PENDING,
    MESSAGE_STATUS_QUEUED,
    OutreachCampaign,
    OutreachMessage,
)
from app.tasks import outreach_tasks


def _campaign_with_message(db, *, campaign_status: str, message_status: str):
    organization = Organization(
        name="Outreach boundary org",
        slug=f"outreach-boundary-{id(db)}-{campaign_status}",
    )
    db.add(organization)
    db.flush()
    campaign = OutreachCampaign(
        organization_id=organization.id,
        name="Boundary campaign",
        brief="Backend role",
        status=campaign_status,
    )
    db.add(campaign)
    db.flush()
    message = OutreachMessage(
        organization_id=organization.id,
        campaign_id=campaign.id,
        recipient_name="Candidate",
        email=f"{message_status}@example.com",
        body="Hello {{cta_url}}",
        status=message_status,
    )
    db.add(message)
    db.commit()
    return campaign, message


def test_draft_model_call_has_no_open_worker_transaction(db):
    campaign, message = _campaign_with_message(
        db,
        campaign_status=CAMPAIGN_STATUS_GENERATING,
        message_status=MESSAGE_STATUS_PENDING,
    )
    worker_db = Session(bind=db.get_bind())

    def generate(*args, **kwargs):
        assert worker_db.in_transaction() is False
        return SimpleNamespace(
            ok=True,
            value=SimpleNamespace(subject="Subject", body="Body {{cta_url}}"),
            error_reason=None,
        )

    with (
        patch("app.platform.database.SessionLocal", return_value=worker_db),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=MagicMock(),
        ),
        patch("app.llm.structured.generate_structured", side_effect=generate),
    ):
        result = outreach_tasks.generate_campaign_drafts.run(int(campaign.id))

    assert result == {"ok": True, "drafted": 1, "failed": 0}
    db.expire_all()
    assert db.get(OutreachMessage, int(message.id)).status == "draft"


def test_email_provider_call_has_no_open_worker_transaction(db):
    campaign, message = _campaign_with_message(
        db,
        campaign_status=CAMPAIGN_STATUS_SENDING,
        message_status=MESSAGE_STATUS_QUEUED,
    )
    worker_db = Session(bind=db.get_bind())
    email_service = MagicMock()

    def send(**kwargs):
        assert worker_db.in_transaction() is False
        return {"success": True, "email_id": "outreach-boundary-email"}

    email_service.send_outreach_email.side_effect = send
    with (
        patch("app.platform.database.SessionLocal", return_value=worker_db),
        patch(
            "app.components.notifications.email_client.EmailService",
            return_value=email_service,
        ),
        patch.object(outreach_tasks.time, "sleep", return_value=None),
    ):
        result = outreach_tasks.send_campaign_messages.run(int(campaign.id))

    assert result == {"ok": True, "sent": 1, "suppressed": 0, "failed": 0}
    db.expire_all()
    assert db.get(OutreachMessage, int(message.id)).status == "sent"
