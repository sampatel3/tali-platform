from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# Voluntary EEO / OFCCP self-identification.
#
# SEGREGATION IS THE WHOLE POINT: this lives in its own table with NO
# relationship back into the application/scoring graph, and the service exposes
# ONLY aggregate counts — never a per-candidate read. The scoring/decision agent
# must never see these values (see the "agent never acts on protected
# characteristics" rule). Collected only with the applicant's explicit consent;
# ``declined_to_answer`` records a deliberate non-answer.
class EEOResponse(Base):
    __tablename__ = "eeo_responses"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_eeo_response_application"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    application_id = Column(
        Integer, ForeignKey("candidate_applications.id"), nullable=False
    )
    gender = Column(String, nullable=True)
    race_ethnicity = Column(String, nullable=True)
    veteran_status = Column(String, nullable=True)
    disability_status = Column(String, nullable=True)
    declined_to_answer = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Intentionally NO relationship() to CandidateApplication — nothing that
    # walks the scoring graph should be able to reach this row.
