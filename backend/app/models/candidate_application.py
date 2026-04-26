from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class CandidateApplication(Base):
    __tablename__ = "candidate_applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "role_id", name="uq_candidate_role_application"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    status = Column(String, default="applied", nullable=False)
    pipeline_stage = Column(String, default="applied", nullable=False)
    pipeline_stage_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pipeline_stage_source = Column(String, default="system", nullable=False)
    application_outcome = Column(String, default="open", nullable=False)
    application_outcome_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    external_refs = Column(JSON, nullable=True)
    external_stage_raw = Column(String, nullable=True)
    external_stage_normalized = Column(String, nullable=True)
    integration_sync_state = Column(JSON, nullable=True)
    version = Column(Integer, default=1, nullable=False)
    notes = Column(Text, nullable=True)
    source = Column(String, default="manual", nullable=False)
    workable_candidate_id = Column(String, nullable=True, index=True)
    workable_stage = Column(String, nullable=True)
    workable_sourced = Column(Boolean, nullable=True)
    workable_profile_url = Column(String, nullable=True)
    workable_score_raw = Column(Float, nullable=True)
    workable_score = Column(Float, nullable=True)
    workable_score_source = Column(String, nullable=True)
    rank_score = Column(Float, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # Candidate CV scoped to this role application
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_text = Column(Text, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    cv_match_score = Column(Float, nullable=True)
    cv_match_details = Column(JSON, nullable=True)
    cv_match_scored_at = Column(DateTime(timezone=True), nullable=True)
    pre_screen_score_100 = Column(Float, nullable=True)
    requirements_fit_score_100 = Column(Float, nullable=True)
    pre_screen_recommendation = Column(String, nullable=True)
    pre_screen_evidence = Column(JSON, nullable=True)
    auto_reject_state = Column(String, nullable=True)
    auto_reject_reason = Column(Text, nullable=True)
    auto_reject_triggered_at = Column(DateTime(timezone=True), nullable=True)
    screening_pack = Column(JSON, nullable=True)
    tech_interview_pack = Column(JSON, nullable=True)
    screening_interview_summary = Column(JSON, nullable=True)
    tech_interview_summary = Column(JSON, nullable=True)
    interview_evidence_summary = Column(JSON, nullable=True)
    taali_score_cache_100 = Column(Float, nullable=True)
    assessment_score_cache_100 = Column(Float, nullable=True)
    role_fit_score_cache_100 = Column(Float, nullable=True)
    score_mode_cache = Column(String, nullable=True)
    score_cached_at = Column(DateTime(timezone=True), nullable=True)
    report_share_token = Column(String, nullable=True, unique=True, index=True)
    report_share_created_at = Column(DateTime(timezone=True), nullable=True)

    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    candidate = relationship("Candidate", back_populates="applications")
    organization = relationship("Organization", back_populates="applications")
    role = relationship("Role", back_populates="applications")
    assessments = relationship("Assessment", back_populates="application")
    events = relationship(
        "CandidateApplicationEvent",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CandidateApplicationEvent.created_at.desc()",
    )
    interviews = relationship(
        "ApplicationInterview",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationInterview.linked_at.desc()",
    )
    score_jobs = relationship(
        "CvScoreJob",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CvScoreJob.queued_at.desc()",
    )
