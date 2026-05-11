from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


AGENT_DECISION_TYPES = (
    "advance_to_interview",
    "reject",
    "skip_assessment_reject",
)
# ``reverted_for_feedback`` is set by the "Send back & teach" action — the
# decision goes back into the queue with the reviewer's correction note
# attached, while a ``decision_feedback`` row carries the training signal.
AGENT_DECISION_STATUSES = (
    "pending",
    "approved",
    "overridden",
    "reverted_for_feedback",
    "discarded",
    "expired",
)
# ``human_disposition`` records *what kind* of human action resolved the
# decision, regardless of the lifecycle state. ``approved``/``overridden``
# mirror ``status``; ``taught`` is set when the resolver path was the teach
# loop (regardless of whether the decision is back to pending or applied).
AGENT_DECISION_HUMAN_DISPOSITIONS = (
    "approved",
    "overridden",
    "taught",
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

    # Hub-era fields (migration 063):
    #   feedback_id: links to the latest decision_feedback row when the human
    #     disposition was ``taught``.
    #   human_disposition: orthogonal to status — answers "what kind of human
    #     action resolved this," used by the Hub to compute teach- vs override-
    #     rate without joining decision_feedback every time.
    #   snoozed_until: pending rows are hidden from the queue until this time.
    # ``feedback_id`` and ``decision_feedback.decision_id`` form a mutual FK
    # cycle (a decision points at its current feedback row, the feedback
    # points back at the decision). Mark this side ``use_alter`` so
    # SQLAlchemy can sort table creation/deletion deterministically.
    feedback_id = Column(
        BigInteger,
        ForeignKey("decision_feedback.id", use_alter=True, name="fk_agent_decisions_feedback_id"),
        nullable=True,
    )
    human_disposition = Column(String, nullable=True)
    snoozed_until = Column(DateTime(timezone=True), nullable=True)

    idempotency_key = Column(String, nullable=False)

    # Evidence validation (migration 074): set by
    # ``validate_agent_decision_evidence`` after the row is created.
    # ``validation_status`` is one of: passed / failed / skipped / NULL.
    # ``validation_failures`` is a JSON list of human-readable failure
    # descriptions when status == "failed". A failed validation does
    # not refuse the queue — it surfaces a warning badge to the
    # recruiter so they know cited evidence may be fabricated.
    validation_status = Column(String, nullable=True)
    validation_failures = Column(JSON, nullable=True)

    agent_run = relationship("AgentRun", back_populates="decisions")
    role = relationship("Role")
    application = relationship("CandidateApplication")
