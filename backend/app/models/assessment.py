from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Float, Text, Enum, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base
import enum


class AssessmentStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    task_id = Column(Integer, ForeignKey("tasks.id"))
    token = Column(String, unique=True, index=True)
    status = Column(Enum(AssessmentStatus), default=AssessmentStatus.PENDING)
    duration_minutes = Column(Integer, default=30)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    score = Column(Float)
    tests_passed = Column(Integer)
    tests_total = Column(Integer)
    code_quality_score = Column(Float)
    time_efficiency_score = Column(Float)
    ai_usage_score = Column(Float)
    test_results = Column(JSON)
    ai_prompts = Column(JSON)
    code_snapshots = Column(JSON)
    timeline = Column(JSON)
    e2b_session_id = Column(String)
    workable_candidate_id = Column(String)
    workable_job_id = Column(String)
    posted_to_workable = Column(Boolean, default=False)
    posted_to_workable_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="assessments")
    candidate = relationship("Candidate", back_populates="assessments")
    task = relationship("Task")
    sessions = relationship("AssessmentSession", back_populates="assessment")
