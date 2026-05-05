from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


AGENT_DECISION_TYPES = (
    "advance_to_interview",
    "reject",
    "skip_assessment_reject",
)
AGENT_DECISION_STATUSES = (
    "pending",
    "approved",
    "overridden",
    "discarded",
    "expired",
)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_decisions_idempotency_key"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    agent_run_id = Column(BigInteger, ForeignKey("agent_runs.id"), nullable=True, index=True)

    decision_type = Column(String, nullable=False)
    recommendation = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)

    reasoning = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    confidence = Column(Numeric(4, 3), nullable=True)

    model_version = Column(String, nullable=False)
    prompt_version = Column(String, nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    override_action = Column(String, nullable=True)

    idempotency_key = Column(String, nullable=False)

    agent_run = relationship("AgentRun", back_populates="decisions")
    role = relationship("Role")
    application = relationship("CandidateApplication")
