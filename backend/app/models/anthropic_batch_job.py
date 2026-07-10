"""One row per Message Batches API submission — the metering anchor.

The Batches API splits one logical operation across processes and time:
the submitting worker knows the attribution (feature, org, per-request
entity ids) but has no token usage yet; the polling worker sees the usage
in the results stream but — without this row — would know nothing about
who to bill. ``MeteredAnthropicClient`` writes this row at
``messages.batches.create`` time and reads it back at
``messages.batches.results`` time, so every batch result lands a
``claude_call_log`` + ``usage_events`` pair priced at the batch tier
(50% of standard) with the right attribution.

``metered_at`` doubles as the idempotency latch: ``results()`` may be
called repeatedly (polling, retries, ops spelunking) but only the first
full pass writes metering rows.

``context`` holds the per-custom_id attribution map supplied at submit
time (``{custom_id: {"entity_id": ..., "role_id": ..., "user_id": ...}}``).
It is advisory — a missing entry degrades attribution, never capture.
"""
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class AnthropicBatchJob(Base):
    __tablename__ = "anthropic_batch_jobs"

    id = Column(Integer, primary_key=True, index=True)
    # Anthropic's batch id (``msgbatch_...``). Unique — one row per batch.
    batch_id = Column(String, unique=True, index=True, nullable=False)
    # A batch goes through ONE API key, so it is single-org by construction
    # (multi-org batches would need splitting — deliberately unsupported).
    # Nullable for shared-key batches with no org context; those still get
    # claude_call_log rows, just no usage_events (which require an org).
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    # pricing_service.Feature value for every request in the batch.
    feature = Column(String, nullable=False)
    model = Column(String, nullable=True)
    request_count = Column(Integer, nullable=False, default=0)
    # submitted | ended | canceled | expired | failed. Advisory (updated by
    # the polling task); the metering latch is ``metered_at``, not this.
    status = Column(String, nullable=False, default="submitted")
    # Per-custom_id attribution map captured at submit time.
    context = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set once, when results() has metered every entry. The idempotency
    # latch — a non-null value means a second results() pass records nothing.
    metered_at = Column(DateTime(timezone=True), nullable=True)
    metered_count = Column(Integer, nullable=False, default=0)
