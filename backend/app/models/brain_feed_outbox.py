"""``brain_feed_outbox`` — durable queue for the outbound mainspring feed.

Tali is the live hiring platform; mainspring is the cross-vertical substrate
it runs on. The connection is bidirectional: component updates flow *in* from
mainspring (vendoring + the WS-E re-vendor bot), and anonymized learning signal
flows *out* from Tali so the substrate's cross-vertical brain can improve.

This table is the outbound hop. A periodic sweep enqueues ANONYMIZED,
aggregable records — resolved agent decisions (agent recommendation + the
human's disposition), teach-loop outcomes, and daily usage/cost rollups — none
of which carry candidate PII, free-text reasoning, role titles, or raw row ids
(see ``app.brain_feed.anonymize``). A Celery drain task then POSTs pending rows
to mainspring's ingest API, idempotent on ``event_id`` so a re-send is a no-op
on both ends.

The whole feature sits behind ``MAINSPRING_BRAIN_FEED_ENABLED`` (default off):
when off nothing is enqueued, so the live platform is completely unaffected.
With the flag on but no ingest URL configured, the drain runs in shadow
(log-only) — the intended posture until the mainspring ingest endpoint is live.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# Record kinds the feed knows how to build + ship. Singular here; the drain
# maps each to its plural mainspring ingest path (decision -> /ingest/decisions).
BRAIN_FEED_KIND_DECISION = "decision"
BRAIN_FEED_KIND_OUTCOME = "outcome"
BRAIN_FEED_KIND_USAGE = "usage"
BRAIN_FEED_KINDS = (
    BRAIN_FEED_KIND_DECISION,
    BRAIN_FEED_KIND_OUTCOME,
    BRAIN_FEED_KIND_USAGE,
)

# Row lifecycle (mirrors graph_episode_outbox).
BRAIN_FEED_STATUS_PENDING = "pending"
BRAIN_FEED_STATUS_PROCESSING = "processing"
BRAIN_FEED_STATUS_SENT = "sent"
BRAIN_FEED_STATUS_FAILED = "failed"
BRAIN_FEED_STATUSES = (
    BRAIN_FEED_STATUS_PENDING,
    BRAIN_FEED_STATUS_PROCESSING,
    BRAIN_FEED_STATUS_SENT,
    BRAIN_FEED_STATUS_FAILED,
)


class BrainFeedOutbox(Base):
    __tablename__ = "brain_feed_outbox"

    id = Column(BigInteger, primary_key=True)
    # One of BRAIN_FEED_KINDS — selects the mainspring ingest path at drain time.
    record_kind = Column(String(16), nullable=False)
    # Client-stable idempotency key (e.g. "decision-123", "outcome-45",
    # "usage-2026-05-29-score-claude-haiku-4-5"). Unique so a re-sweep of the
    # same source row is a no-op; mainspring also dedups on it as a backstop.
    event_id = Column(String(255), nullable=False, unique=True, index=True)
    # Anonymized, no-PII payload (see app.brain_feed.anonymize). JSON-serialisable.
    payload = Column(JSON, nullable=False)

    status = Column(
        String(16), nullable=False, server_default=BRAIN_FEED_STATUS_PENDING, index=True
    )
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)

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
    "BrainFeedOutbox",
    "BRAIN_FEED_KIND_DECISION",
    "BRAIN_FEED_KIND_OUTCOME",
    "BRAIN_FEED_KIND_USAGE",
    "BRAIN_FEED_KINDS",
    "BRAIN_FEED_STATUS_PENDING",
    "BRAIN_FEED_STATUS_PROCESSING",
    "BRAIN_FEED_STATUS_SENT",
    "BRAIN_FEED_STATUS_FAILED",
    "BRAIN_FEED_STATUSES",
]
