"""Persistent alternate-role scores over canonical ATS applications."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

SISTER_EVAL_PENDING = "pending"
SISTER_EVAL_RUNNING = "running"
SISTER_EVAL_RETRY_WAIT = "retry_wait"
SISTER_EVAL_DONE = "done"
SISTER_EVAL_ERROR = "error"
SISTER_EVAL_UNSCORABLE = "unscorable"
SISTER_EVAL_EXCLUDED = "excluded"
SISTER_EVAL_STALE = "stale"
SISTER_EVAL_STATUSES = {
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_DONE,
    SISTER_EVAL_ERROR,
    SISTER_EVAL_UNSCORABLE,
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_STALE,
}


class SisterRoleEvaluation(Base):
    """Role-owned workflow state over one canonical ATS application.

    The source application keeps the provider identifiers and the shared ATS
    outcome.  Each related role owns its alternate score and Taali pipeline
    stage here, allowing the same candidate to progress differently in each
    role without cloning or forking the provider application.
    """

    __tablename__ = "sister_role_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "source_application_id",
            name="uq_sister_evaluations_role_application",
        ),
        Index("ix_sister_evaluations_role_status", "role_id", "status"),
        Index(
            "ix_sister_evaluations_role_pipeline_stage", "role_id", "pipeline_stage"
        ),
        Index("ix_sister_evaluations_recovery", "status", "next_attempt_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    source_application_id = Column(
        Integer, ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status = Column(String(length=16), nullable=False, default=SISTER_EVAL_PENDING)
    pipeline_stage = Column(
        String(length=32), nullable=False, default="applied", server_default="applied"
    )
    pipeline_stage_updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    pipeline_stage_source = Column(
        String(length=16), nullable=False, default="system", server_default="system"
    )
    spec_fingerprint = Column(String(length=64), nullable=False)
    cv_fingerprint = Column(String(length=64), nullable=True)
    role_fit_score = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)
    # Compact audit trail of superseded results. The current score stays in the
    # first-class columns for fast ranking; prior scores, summaries, and
    # spec/CV fingerprints remain inspectable without cloning applications.
    history = Column(JSON, nullable=True)
    model_version = Column(String(length=100), nullable=True)
    prompt_version = Column(String(length=100), nullable=True)
    trace_id = Column(String(length=100), nullable=True)
    cache_hit = Column(Boolean, nullable=False, default=False, server_default="false")
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_attempted_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(length=100), nullable=True)
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role = relationship("Role")
    source_application = relationship("CandidateApplication")
