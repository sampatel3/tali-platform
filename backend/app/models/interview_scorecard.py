from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# A hire/no-hire signal on a structured scorecard. Ordered strong_no..strong_yes
# for tallying; ``no_decision`` is an explicit abstention (kept out of the lean).
SCORECARD_STRONG_NO = "strong_no"
SCORECARD_NO = "no"
SCORECARD_YES = "yes"
SCORECARD_STRONG_YES = "strong_yes"
SCORECARD_NO_DECISION = "no_decision"
SCORECARD_RECOMMENDATIONS = (
    SCORECARD_STRONG_NO,
    SCORECARD_NO,
    SCORECARD_YES,
    SCORECARD_STRONG_YES,
    SCORECARD_NO_DECISION,
)


class InterviewScorecard(Base):
    """One interviewer's structured feedback on an application. Drafts until
    ``submitted_at`` is set. ``competencies`` is a list of
    ``{name, rating, comment}`` (per-area scores); ``recommendation`` is the
    overall hire signal. Optionally tied to a specific ``ApplicationInterview``
    (a linked meeting) — null when it's standalone feedback.
    """

    __tablename__ = "interview_scorecards"
    __table_args__ = (
        Index("ix_interview_scorecards_application", "application_id"),
        Index("ix_interview_scorecards_org", "organization_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    application_id = Column(
        Integer, ForeignKey("candidate_applications.id"), nullable=False
    )
    interview_id = Column(
        Integer, ForeignKey("application_interviews.id"), nullable=True
    )
    interviewer_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    recommendation = Column(String, nullable=True)
    overall_rating = Column(Integer, nullable=True)  # 1..4
    competencies = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    application = relationship("CandidateApplication")
