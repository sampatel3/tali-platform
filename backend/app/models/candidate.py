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

    # Rich profile fields (populated from Workable payload)
    headline = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    location_city = Column(String, nullable=True)
    location_country = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    profile_url = Column(String, nullable=True)
    social_profiles = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    skills = Column(JSON, nullable=True)
    education_entries = Column(JSON, nullable=True)
    experience_entries = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    workable_enriched = Column(Boolean, default=False)
    workable_created_at = Column(DateTime(timezone=True), nullable=True)

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
