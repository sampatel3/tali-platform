"""Durable, idempotent inbox for Fireflies transcription webhooks."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from ..platform.database import Base


FIREFLIES_INBOX_PENDING = "pending"
FIREFLIES_INBOX_PROCESSING = "processing"
FIREFLIES_INBOX_LINKED = "linked"
FIREFLIES_INBOX_IGNORED = "ignored"
FIREFLIES_INBOX_REVIEW_REQUIRED = "review_required"
FIREFLIES_INBOX_FAILED = "failed"
FIREFLIES_INBOX_TERMINAL = (
    FIREFLIES_INBOX_LINKED,
    FIREFLIES_INBOX_IGNORED,
    FIREFLIES_INBOX_REVIEW_REQUIRED,
    FIREFLIES_INBOX_FAILED,
)


class FirefliesWebhookInbox(Base):
    __tablename__ = "fireflies_webhook_inbox"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "meeting_id",
            "event_type",
            name="uq_fireflies_inbox_org_meeting_event",
        ),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    meeting_id = Column(String(200), nullable=False)
    event_type = Column(String(200), nullable=False)
    # The signed provider event is retained for operational review. Transcript
    # contents are fetched by the worker and remain in ApplicationInterview.
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, server_default=FIREFLIES_INBOX_PENDING, index=True)
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "FirefliesWebhookInbox",
    "FIREFLIES_INBOX_PENDING",
    "FIREFLIES_INBOX_PROCESSING",
    "FIREFLIES_INBOX_LINKED",
    "FIREFLIES_INBOX_IGNORED",
    "FIREFLIES_INBOX_REVIEW_REQUIRED",
    "FIREFLIES_INBOX_FAILED",
    "FIREFLIES_INBOX_TERMINAL",
]
