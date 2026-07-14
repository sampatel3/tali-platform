"""Durable application-created intents for autonomous ATS intake.

Workable and Bullhorn imports run inside a caller-owned transaction.  The
application row is not visible to a worker (or to ``SessionLocal``) until that
transaction commits, so an inline broker dispatch can silently observe
``not_found`` and lose the first parse/score attempt.  This row is written in
the same transaction as the application and drained only after commit.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import false as sql_false
from sqlalchemy.sql.expression import true as sql_true

from ..platform.database import Base


APPLICATION_CREATED_PENDING = "pending"
APPLICATION_CREATED_DISPATCHING = "dispatching"
APPLICATION_CREATED_COMPLETE = "complete"
APPLICATION_CREATED_OUTBOX_STATUSES = (
    APPLICATION_CREATED_PENDING,
    APPLICATION_CREATED_DISPATCHING,
    APPLICATION_CREATED_COMPLETE,
)


class ApplicationCreatedOutbox(Base):
    """One idempotent post-commit intake event per application."""

    __tablename__ = "application_created_outbox"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id = Column(
        Integer,
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    source = Column(String(32), nullable=False)
    score_requested = Column(
        Boolean, nullable=False, default=False, server_default=sql_false()
    )
    paid_work_requested = Column(
        Boolean, nullable=False, default=False, server_default=sql_false()
    )
    requires_active_agent = Column(
        Boolean, nullable=False, default=True, server_default=sql_true()
    )
    parse_origin = Column(String(32), nullable=True)

    status = Column(
        String(16),
        nullable=False,
        default=APPLICATION_CREATED_PENDING,
        server_default=APPLICATION_CREATED_PENDING,
        index=True,
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    # Per-effect receipts make a retry safe after a worker dies between a
    # successful broker publish and the final outbox completion commit.
    auto_reject_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    cv_parse_dispatch_status = Column(String(32), nullable=True)
    cv_parse_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    cv_parse_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    cv_parse_claimed_at = Column(DateTime(timezone=True), nullable=True)
    cv_parse_next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    cv_parse_last_error = Column(Text, nullable=True)
    score_dispatch_status = Column(String(32), nullable=True)
    score_job_id = Column(
        Integer,
        ForeignKey("cv_score_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "ApplicationCreatedOutbox",
    "APPLICATION_CREATED_PENDING",
    "APPLICATION_CREATED_DISPATCHING",
    "APPLICATION_CREATED_COMPLETE",
    "APPLICATION_CREATED_OUTBOX_STATUSES",
]
