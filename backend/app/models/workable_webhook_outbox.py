"""``workable_webhook_outbox`` — durable queue for result callbacks to Workable.

When Taali runs as a Workable Assessments Provider, results are pushed back to
Workable by ``PUT``-ing to the per-assessment ``callback_url``. This outbox is
the durable hop: a periodic sweep enqueues a row per lifecycle event (pending →
completed), and a Celery drain ``PUT``s pending rows — idempotent on
``dedup_key`` so a transient Workable outage never loses a result. Mirrors
``brain_feed_outbox`` / ``graph_episode_outbox``.

Gated by ``WORKABLE_PROVIDER_ENABLED`` (default off): the drain is a no-op
until the integration is deliberately turned on, so the live platform is
unaffected. (Integer PK — low-volume — so SQLite autoincrements it in tests
without the BigInteger workaround the high-volume outboxes need.)
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# Assessment lifecycle events we deliver to Workable (its callback statuses).
WORKABLE_OUTBOX_KIND_PENDING = "pending"
WORKABLE_OUTBOX_KIND_COMPLETED = "completed"
WORKABLE_OUTBOX_KIND_EXPIRED = "expired"
WORKABLE_OUTBOX_KINDS = (
    WORKABLE_OUTBOX_KIND_PENDING,
    WORKABLE_OUTBOX_KIND_COMPLETED,
    WORKABLE_OUTBOX_KIND_EXPIRED,
)

# Row lifecycle (mirrors the other outboxes).
WORKABLE_OUTBOX_STATUS_PENDING = "pending"
WORKABLE_OUTBOX_STATUS_SENT = "sent"
WORKABLE_OUTBOX_STATUS_FAILED = "failed"
WORKABLE_OUTBOX_STATUSES = (
    WORKABLE_OUTBOX_STATUS_PENDING,
    WORKABLE_OUTBOX_STATUS_SENT,
    WORKABLE_OUTBOX_STATUS_FAILED,
)


class WorkableWebhookOutbox(Base):
    __tablename__ = "workable_webhook_outbox"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    # One of WORKABLE_OUTBOX_KINDS — the Workable callback ``status`` to send.
    event_kind = Column(String(32), nullable=False)
    # Idempotency key, e.g. "wkb-assessment-123-completed". Unique so a
    # re-sweep is a no-op; Workable also tolerates re-delivery.
    dedup_key = Column(String(255), nullable=False, unique=True, index=True)
    # Workable's per-assessment callback URL (PUT target).
    callback_url = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)

    status = Column(
        String(16),
        nullable=False,
        server_default=WORKABLE_OUTBOX_STATUS_PENDING,
        index=True,
    )
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "WorkableWebhookOutbox",
    "WORKABLE_OUTBOX_KIND_PENDING",
    "WORKABLE_OUTBOX_KIND_COMPLETED",
    "WORKABLE_OUTBOX_KIND_EXPIRED",
    "WORKABLE_OUTBOX_KINDS",
    "WORKABLE_OUTBOX_STATUS_PENDING",
    "WORKABLE_OUTBOX_STATUS_SENT",
    "WORKABLE_OUTBOX_STATUS_FAILED",
    "WORKABLE_OUTBOX_STATUSES",
]
