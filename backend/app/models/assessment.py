from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Float, Text, Enum, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..platform.database import Base
import enum


class AssessmentStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), index=True)
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
    # Prompt scoring fields (Phase 2)
    prompt_quality_score = Column(Float, nullable=True)
    prompt_efficiency_score = Column(Float, nullable=True)
    independence_score = Column(Float, nullable=True)
    context_utilization_score = Column(Float, nullable=True)
    design_thinking_score = Column(Float, nullable=True)
    debugging_strategy_score = Column(Float, nullable=True)
    written_communication_score = Column(Float, nullable=True)
    learning_velocity_score = Column(Float, nullable=True)
    error_recovery_score = Column(Float, nullable=True)
    requirement_comprehension_score = Column(Float, nullable=True)
    calibration_score = Column(Float, nullable=True)
    prompt_fraud_flags = Column(JSON, nullable=True)
    prompt_analytics = Column(JSON, nullable=True)
    browser_focus_ratio = Column(Float, nullable=True)
    tab_switch_count = Column(Integer, default=0)
    time_to_first_prompt_seconds = Column(Integer, nullable=True)
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    final_score = Column(Float, nullable=True)
    score_breakdown = Column(JSON, nullable=True)
    score_weights_used = Column(JSON, nullable=True)
    flags = Column(JSON, nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)
    total_duration_seconds = Column(Integer, nullable=True)
    total_prompts = Column(Integer, nullable=True)
    total_input_tokens = Column(Integer, nullable=True)
    total_output_tokens = Column(Integer, nullable=True)
    tests_run_count = Column(Integer, nullable=True)
    tests_pass_count = Column(Integer, nullable=True)
    # CV-Job fit matching (Phase 2)
    cv_job_match_score = Column(Float, nullable=True)
    cv_job_match_details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="assessments")
    candidate = relationship("Candidate", back_populates="assessments")
    task = relationship("Task")
    sessions = relationship("AssessmentSession", back_populates="assessment")
