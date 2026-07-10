from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from ..platform.database import Base

# Overall recommendation values, ordered strongest-negative → strongest-positive.
# The calibration script maps these to a numeric band (strong_no=-2 … strong_yes=2).
INTERVIEW_RECOMMENDATIONS = ("strong_yes", "yes", "neutral", "no", "strong_no")


class InterviewFeedback(Base):
    """A recruiter's structured record of what happened in one interview.

    Joins a Taali score (via the application) to a human interview result so
    the calibration script can measure predictive validity. Denormalizes
    ``role_id`` for per-role reporting without a second join.
    """

    __tablename__ = "interview_feedback"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    interviewer_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    interviewer_name = Column(String, nullable=True)
    interview_round = Column(String, nullable=False, default="interview")
    overall_recommendation = Column(String, nullable=False)
    # Optional 1–5 ratings keyed by the 5-Ds axes (delegation/description/
    # discernment/diligence/deliverable).
    dimension_ratings = Column(JSON, nullable=True)
    # List of {criterion_id, criterion_text, result} where result is
    # confirmed/refuted/not_probed — ties back to the interview kit's
    # priority_probes / knockout_checks.
    probe_results = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
