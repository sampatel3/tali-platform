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
SISTER_EVAL_DONE = "done"
SISTER_EVAL_ERROR = "error"
SISTER_EVAL_UNSCORABLE = "unscorable"
SISTER_EVAL_STATUSES = {
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_DONE,
    SISTER_EVAL_ERROR,
    SISTER_EVAL_UNSCORABLE,
}


class SisterRoleEvaluation(Base):
    """The current sister-role evaluation for one source application.

    The source application retains ATS stage, outcome, notes, and identifiers.
    This row only owns the alternate fit result, so Workable state can never
    fork between two Taali role rows.
    """

    __tablename__ = "sister_role_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "source_application_id",
            name="uq_sister_evaluations_role_application",
        ),
        Index("ix_sister_evaluations_role_status", "role_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_application_id = Column(
        Integer, ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status = Column(String(length=16), nullable=False, default=SISTER_EVAL_PENDING)
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
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role = relationship("Role")
    source_application = relationship("CandidateApplication")
