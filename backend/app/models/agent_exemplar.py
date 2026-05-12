"""``agent_exemplars`` — per-sub-agent training-example store.

One row per teach-attributed correction. The sub-agent retrieves
top-k similar rows at score time and injects them as few-shot in
its prompt — see ``agent_runtime.exemplar_store``.

D4 eviction (nightly): when an agent's exemplar count exceeds the
cap, lowest-scoring rows are deleted. Score is::

    score = -age_in_days + (correction_magnitude * 30) + (use_count * 5)

Higher = keep. The cap defaults to 500 per (agent, org, role).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class AgentExemplar(Base):
    __tablename__ = "agent_exemplars"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True, index=True)
    agent_name = Column(String(32), nullable=False, index=True)

    source_feedback_id = Column(
        BigInteger, ForeignKey("decision_feedback.id"), nullable=True
    )

    # Canonical feature vector — keys = canonical signal names, values
    # = floats in roughly [0, 100] for scores and {0, 1} for booleans.
    features_json = Column(JSON, nullable=False)

    agent_score = Column(Float, nullable=False)
    corrected_score = Column(Float, nullable=True)
    direction = Column(String(8), nullable=True)
    attributed_reason = Column(Text, nullable=True)

    use_count = Column(Integer, nullable=False, server_default="0")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_feedback = relationship("DecisionFeedback", foreign_keys=[source_feedback_id])
