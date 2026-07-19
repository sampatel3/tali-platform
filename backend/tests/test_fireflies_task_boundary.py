"""Fireflies inbox workers do not hold SQL while fetching transcripts."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models.fireflies_webhook_inbox import FirefliesWebhookInbox
from app.models.organization import Organization
from app.services.fireflies_inbox_service import process_one


def test_transcript_fetch_runs_after_claim_transaction_is_released(db):
    organization = Organization(
        name="Fireflies boundary org",
        slug=f"fireflies-boundary-{id(db)}",
        fireflies_api_key_encrypted="encrypted-api-key",
        fireflies_owner_email="owner@example.com",
    )
    db.add(organization)
    db.flush()
    inbox = FirefliesWebhookInbox(
        organization_id=organization.id,
        meeting_id="meeting-boundary",
        event_type="Transcription completed",
        payload={},
        status="pending",
    )
    db.add(inbox)
    db.commit()
    worker_db = Session(bind=db.get_bind())

    def get_transcript(self, meeting_id):
        assert meeting_id == "meeting-boundary"
        assert worker_db.in_transaction() is False
        return {
            "id": meeting_id,
            "organizer_email": "different-owner@example.com",
            "participants": [],
            "sentences": [],
        }

    with (
        patch(
            "app.services.fireflies_inbox_service.decrypt_integration_secret",
            return_value="decrypted-api-key",
        ),
        patch(
            "app.services.fireflies_inbox_service.FirefliesService.get_transcript",
            new=get_transcript,
        ),
    ):
        result = process_one(worker_db, inbox_id=int(inbox.id))

    assert result["status"] == "ignored"
    assert result["reason"] == "owner_mismatch"


def test_provider_failure_stores_stable_code_not_response_body(db):
    secret = "fireflies-token in upstream response body"
    organization = Organization(
        name="Fireflies privacy org",
        slug=f"fireflies-privacy-{id(db)}",
        fireflies_api_key_encrypted="encrypted-api-key",
    )
    db.add(organization)
    db.flush()
    inbox = FirefliesWebhookInbox(
        organization_id=organization.id,
        meeting_id="meeting-privacy",
        event_type="Transcription completed",
        payload={},
        status="pending",
    )
    db.add(inbox)
    db.commit()

    with (
        patch(
            "app.services.fireflies_inbox_service.decrypt_integration_secret",
            return_value="decrypted-api-key",
        ),
        patch(
            "app.services.fireflies_inbox_service.FirefliesService.get_transcript",
            side_effect=RuntimeError(secret),
        ),
    ):
        result = process_one(db, inbox_id=int(inbox.id), max_attempts=1)

    db.refresh(inbox)
    assert result == {
        "status": "failed",
        "reason": "retry_exhausted",
        "inbox_id": inbox.id,
    }
    assert inbox.last_error == "fireflies_inbox:RuntimeError"
    assert secret not in str(result)
    assert secret not in str(inbox.last_error)
