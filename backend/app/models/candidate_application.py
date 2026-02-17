from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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

    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    candidate = relationship("Candidate", back_populates="applications")
    role = relationship("Role", back_populates="applications")
    assessments = relationship("Assessment", back_populates="application")
