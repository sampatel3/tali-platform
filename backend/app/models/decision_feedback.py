"""``decision_feedback`` — recruiter "Send back & teach" submissions.

One row per teach action. The decision being taught against is reverted to
``pending`` (with status ``reverted_for_feedback``) and this row carries the
reviewer's correction. When ``scope`` is ``role`` or ``org`` the row also
becomes the input to the nightly retune job; ``scope='org'`` requires a
second admin to co-sign before the retune fires.

Lifecycle:

    created_at → (cosign_required ? cosigned_at : skip) → applied_at
                                              \\
                                               → reverted_at (1h grace)

When ``applied_at`` is set, ``applied_revision_id`` points at the
``rubric_revisions`` row produced by the retune.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


FAILURE_MODES = (
    "rubric_mismatch",
    "wrong_threshold",
    "missing_signal",
    "over_confident",
    "policy_violation",
    "other",
)
FEEDBACK_SCOPES = ("decision", "role", "org")


class DecisionFeedback(Base):
    __tablename__ = "decision_feedback"

    id = Column(BigInteger, primary_key=True)
    decision_id = Column(BigInteger, ForeignKey("agent_decisions.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True, index=True)

    failure_mode = Column(String(32), nullable=False)
    correction_text = Column(Text, nullable=False)
    scope = Column(String(16), nullable=False)

    cosign_required = Column(Boolean, nullable=False, server_default=false())
    cosigned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cosigned_at = Column(DateTime(timezone=True), nullable=True)

    applied_at = Column(DateTime(timezone=True), nullable=True)
    applied_revision_id = Column(BigInteger, ForeignKey("rubric_revisions.id"), nullable=True)

    reverted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    decision = relationship(
        "AgentDecision",
        primaryjoin="DecisionFeedback.decision_id == AgentDecision.id",
        foreign_keys=[decision_id],
    )
    role = relationship("Role")
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    cosigner = relationship("User", foreign_keys=[cosigned_by_user_id])
    applied_revision = relationship(
        "RubricRevision",
        primaryjoin="DecisionFeedback.applied_revision_id == RubricRevision.id",
        foreign_keys=[applied_revision_id],
    )
