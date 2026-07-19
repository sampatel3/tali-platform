"""Durable idempotency item for one application in a pre-screen batch."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


PRESCREEN_BATCH_ITEM_QUEUED = "queued"
PRESCREEN_BATCH_ITEM_ATTEMPTING = "attempting"
PRESCREEN_BATCH_ITEM_DONE = "done"
PRESCREEN_BATCH_ITEM_ERROR = "error"
PRESCREEN_BATCH_ITEM_SKIPPED = "skipped"
PRESCREEN_BATCH_ITEM_AMBIGUOUS = "ambiguous"
PRESCREEN_BATCH_ITEM_TERMINAL = (
    PRESCREEN_BATCH_ITEM_DONE,
    PRESCREEN_BATCH_ITEM_ERROR,
    PRESCREEN_BATCH_ITEM_SKIPPED,
    PRESCREEN_BATCH_ITEM_AMBIGUOUS,
)


class PrescreenBatchItem(Base):
    __tablename__ = "prescreen_batch_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "application_id", name="uq_prescreen_batch_item_run_app"
        ),
        Index("ix_prescreen_batch_items_run_status", "run_id", "status"),
        Index(
            "ix_prescreen_batch_items_recovery",
            "status",
            "dispatch_lease_until",
        ),
        Index(
            "ix_prescreen_batch_items_attempt_recovery",
            "status",
            "provider_attempt_started_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer, ForeignKey("background_job_runs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    application_id = Column(
        Integer, ForeignKey("candidate_applications.id"), nullable=False, index=True
    )
    status = Column(
        String(16), nullable=False, default=PRESCREEN_BATCH_ITEM_QUEUED
    )
    error_code = Column(String(80), nullable=True)
    # Dispatch is a DB-backed lease rather than an in-memory promise. If the
    # broker accepts ambiguously, the dispatcher dies, or a worker is lost,
    # the still-queued item becomes eligible for bounded re-dispatch after the
    # lease. The token is used for compare-and-swap release/ack updates.
    dispatch_token = Column(String(36), nullable=True)
    dispatch_lease_until = Column(DateTime(timezone=True), nullable=True)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    last_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    # Committed before the paid call. If the worker vanishes after a provider
    # response but before the application transaction commits, recovery marks
    # this attempt ambiguous and surfaces it instead of silently paying again.
    provider_attempt_token = Column(String(36), nullable=True)
    provider_attempt_started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "PRESCREEN_BATCH_ITEM_DONE",
    "PRESCREEN_BATCH_ITEM_ERROR",
    "PRESCREEN_BATCH_ITEM_AMBIGUOUS",
    "PRESCREEN_BATCH_ITEM_ATTEMPTING",
    "PRESCREEN_BATCH_ITEM_QUEUED",
    "PRESCREEN_BATCH_ITEM_SKIPPED",
    "PRESCREEN_BATCH_ITEM_TERMINAL",
    "PrescreenBatchItem",
]
