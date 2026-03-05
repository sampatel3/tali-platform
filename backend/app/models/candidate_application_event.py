from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class CandidateApplicationEvent(Base):
    __tablename__ = "candidate_application_events"
    __table_args__ = (
        UniqueConstraint("application_id", "idempotency_key", name="uq_application_event_idempotency_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    event_type = Column(String, nullable=False)
    from_stage = Column(String, nullable=True)
    to_stage = Column(String, nullable=True)
    from_outcome = Column(String, nullable=True)
    to_outcome = Column(String, nullable=True)
    actor_type = Column(String, nullable=False, default="system")
    actor_id = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSON, nullable=True)
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application = relationship("CandidateApplication", back_populates="events")
