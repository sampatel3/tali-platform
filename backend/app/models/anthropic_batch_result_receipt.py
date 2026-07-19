"""Immutable, per-result metering receipts for Anthropic Message Batches."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class AnthropicBatchResultReceipt(Base):
    """Terminal local metering outcome for one provider batch result.

    The unique ``(batch_job_id, custom_id)`` identity is the durable
    idempotency seam.  Rows are inserted in the same transaction as their
    UsageEvent and ClaudeCallLog and are never updated by application code.
    """

    __tablename__ = "anthropic_batch_result_receipts"

    id = Column(Integer, primary_key=True)
    batch_job_id = Column(
        Integer,
        ForeignKey("anthropic_batch_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    custom_id = Column(String, nullable=False)
    state = Column(String(length=16), nullable=False)
    result_type = Column(String(length=64), nullable=False)
    usage_event_id = Column(
        Integer,
        ForeignKey("usage_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    call_log_id = Column(
        BigInteger,
        ForeignKey("claude_call_log.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_message_id = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "batch_job_id",
            "custom_id",
            name="uq_anthropic_batch_result_receipt_identity",
        ),
        UniqueConstraint(
            "provider_message_id",
            name="uq_anthropic_batch_result_receipt_provider_message",
        ),
        CheckConstraint(
            "state IN ('metered', 'skipped')",
            name="ck_anthropic_batch_result_receipts_state",
        ),
        CheckConstraint(
            "state = 'skipped' OR call_log_id IS NOT NULL",
            name="ck_anthropic_batch_result_receipts_metered_call_log",
        ),
    )


__all__ = ["AnthropicBatchResultReceipt"]
