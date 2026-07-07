from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Canonical outbound event types. A subscription with an empty ``event_types``
# receives all of them; otherwise only the ones it lists. New types can be added
# freely — this tuple is the documented set, not an enforced enum.
WEBHOOK_EVENT_APPLICATION_CREATED = "application.created"
WEBHOOK_EVENT_APPLICATION_STAGE_CHANGED = "application.stage_changed"
WEBHOOK_EVENT_OFFER_ACCEPTED = "offer.accepted"
WEBHOOK_EVENT_OFFER_SENT = "offer.sent"
WEBHOOK_EVENT_TYPES = (
    WEBHOOK_EVENT_APPLICATION_CREATED,
    WEBHOOK_EVENT_APPLICATION_STAGE_CHANGED,
    WEBHOOK_EVENT_OFFER_ACCEPTED,
    WEBHOOK_EVENT_OFFER_SENT,
)

# Delivery lifecycle.
DELIVERY_PENDING = "pending"
DELIVERY_DELIVERED = "delivered"
DELIVERY_FAILED = "failed"


class WebhookSubscription(Base):
    """An org's outbound webhook endpoint. ``secret`` signs the payload (HMAC-
    SHA256) so the receiver can verify authenticity. ``event_types`` is the
    subscribed set — empty means every event."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        Index("ix_webhook_subscriptions_org", "organization_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    event_types = Column(JSON, nullable=True)  # list[str]; empty/None = all
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    deliveries = relationship(
        "WebhookDelivery", back_populates="subscription", cascade="all, delete-orphan"
    )


class WebhookDelivery(Base):
    """One attempt-tracked delivery of one event to one subscription. Kept as an
    audit trail (and a retry work-list) independent of the subscription."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_sub", "subscription_id"),
        Index("ix_webhook_deliveries_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    subscription_id = Column(
        Integer, ForeignKey("webhook_subscriptions.id"), nullable=False
    )
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default=DELIVERY_PENDING)
    attempts = Column(Integer, nullable=False, default=0)
    response_status = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    subscription = relationship("WebhookSubscription", back_populates="deliveries")
