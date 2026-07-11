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
    # Bullhorn ATS identity + raw payload (see docs/BULLHORN_BUILD_PLAN.md §3).
    bullhorn_candidate_id = Column(String, nullable=True, index=True)
    bullhorn_data = Column(JSON, nullable=True)

    # Rich profile fields (populated from Workable payload)
    headline = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    location_city = Column(String, nullable=True)
    location_country = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    # Last 9 digits of ``phone``, country-code/formatting stripped. Used as a
    # dedup fallback when the same person applies under a second email — same
    # human, different email, so workable_candidate_id + email both miss.
    # Indexed for equality lookup during sync.
    phone_normalized = Column(String, nullable=True, index=True)
    profile_url = Column(String, nullable=True)
    social_profiles = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    skills = Column(JSON, nullable=True)
    education_entries = Column(JSON, nullable=True)
    experience_entries = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    # Recruiter comments and activity log fetched from
    # /candidates/{id}/comments and /candidates/{id}/activities. Surfaced to
    # the pre-screen prompt so hard constraints expressed only in Workable
    # (e.g. salary expectation in a recruiter note) are visible to the LLM.
    workable_comments = Column(JSON, nullable=True)
    workable_activities = Column(JSON, nullable=True)
    workable_enriched = Column(Boolean, default=False)
    workable_created_at = Column(DateTime(timezone=True), nullable=True)

    # CV fields (uploaded by recruiter or candidate)
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_text = Column(Text, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    # Parsed CV sections (cv_parsing module). Mirrors candidate_applications.cv_sections.
    cv_sections = Column(JSON, nullable=True)

    # Job specification (uploaded by recruiter)
    job_spec_file_url = Column(String, nullable=True)
    job_spec_filename = Column(String, nullable=True)
    job_spec_text = Column(Text, nullable=True)
    job_spec_uploaded_at = Column(DateTime(timezone=True), nullable=True)

    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    applications = relationship("CandidateApplication", back_populates="candidate", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="candidate")
