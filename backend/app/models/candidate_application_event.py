from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
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


class CandidateApplicationEvent(Base):
    __tablename__ = "candidate_application_events"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "idempotency_key",
            name="uq_application_event_idempotency_key",
        ),
        Index(
            "ix_application_events_org_role_created",
            "organization_id",
            "role_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    # The logical product role that owns this action.  This can differ from the
    # application row's persistence role for a related-role membership. Rows
    # predating migration 186 remain NULL because the ledger is append-only;
    # history readers resolve those rows from immutable legacy provenance.
    role_id = Column(
        Integer,
        ForeignKey("roles.id", name="fk_candidate_application_events_role_id"),
        index=True,
        nullable=True,
    )
    # Optional link to the recommendation/resolution that caused the action.
    # Direct recruiter and provider-sync actions intentionally leave it null.
    agent_decision_id = Column(
        BigInteger,
        ForeignKey(
            "agent_decisions.id",
            use_alter=True,
            name="fk_candidate_application_events_agent_decision_id",
        ),
        index=True,
        nullable=True,
    )
    event_type = Column(String, nullable=False)
    from_stage = Column(String, nullable=True)
    to_stage = Column(String, nullable=True)
    from_outcome = Column(String, nullable=True)
    to_outcome = Column(String, nullable=True)
    actor_type = Column(String, nullable=False, default="system")
    actor_id = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSON, nullable=True)
    # First-class action provenance used by exact history filters.  Generic
    # pipeline stages (for example ``advanced``) are not a substitute for the
    # recruiter-selected ATS destination (for example ``Technical Interview``).
    target_stage = Column(String, nullable=True)
    effect_status = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application = relationship("CandidateApplication", back_populates="events")
