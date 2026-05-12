"""``graph_writeback_queue`` — pending co-sign rows for Phase 6."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


GRAPH_WRITEBACK_STATUSES = (
    "pending_cosign",
    "committed",
    "rejected",
    "blocked",
)

GRAPH_WRITEBACK_SENSITIVITIES = ("low", "medium", "high")


class GraphWritebackQueueItem(Base):
    __tablename__ = "graph_writeback_queue"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    source_feedback_id = Column(
        BigInteger, ForeignKey("decision_feedback.id"), nullable=False, index=True
    )
    hint_json = Column(JSON, nullable=False)
    sensitivity = Column(String(8), nullable=False)
    status = Column(String(16), nullable=False, server_default="pending_cosign")

    proposed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    proposed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    cosigned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cosigned_at = Column(DateTime(timezone=True), nullable=True)
    cosign_note = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    blocked_reason = Column(Text, nullable=True)

    committed_at = Column(DateTime(timezone=True), nullable=True)
    feedback_episode_uuid = Column(String(128), nullable=True)

    source_feedback = relationship("DecisionFeedback")
