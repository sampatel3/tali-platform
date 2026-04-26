from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class ApplicationInterview(Base):
    __tablename__ = "application_interviews"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    stage = Column(String, nullable=False, default="screening")
    source = Column(String, nullable=False, default="manual")
    provider = Column(String, nullable=True)
    provider_meeting_id = Column(String, nullable=True, index=True)
    provider_url = Column(String, nullable=True)
    status = Column(String, nullable=False, default="linked")
    transcript_text = Column(Text, nullable=True)
    summary = Column(JSON, nullable=True)
    speakers = Column(JSON, nullable=True)
    provider_payload = Column(JSON, nullable=True)
    meeting_date = Column(DateTime(timezone=True), nullable=True)
    linked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    application = relationship("CandidateApplication", back_populates="interviews")
