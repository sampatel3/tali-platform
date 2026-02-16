from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..platform.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    email = Column(String, index=True)
    full_name = Column(String)
    position = Column(String)
    work_email = Column(String, nullable=True, index=True)
    company_name = Column(String, nullable=True)
    company_size = Column(String, nullable=True)
    lead_source = Column(String, nullable=True)
    marketing_consent = Column(Boolean, default=True)
    workable_candidate_id = Column(String)
    workable_data = Column(JSON)

    # CV fields (uploaded by recruiter or candidate)
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_text = Column(Text, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)

    # Job specification (uploaded by recruiter)
    job_spec_file_url = Column(String, nullable=True)
    job_spec_filename = Column(String, nullable=True)
    job_spec_text = Column(Text, nullable=True)
    job_spec_uploaded_at = Column(DateTime(timezone=True), nullable=True)

    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    applications = relationship("CandidateApplication", back_populates="candidate", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="candidate")
