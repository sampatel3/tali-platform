from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base


class AssessmentSession(Base):
    __tablename__ = "assessment_sessions"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"))
    session_start = Column(DateTime(timezone=True), server_default=func.now())
    session_end = Column(DateTime(timezone=True))
    keystrokes = Column(Integer, default=0)
    code_executions = Column(Integer, default=0)
    ai_requests = Column(Integer, default=0)
    activity_log = Column(JSON)

    assessment = relationship("Assessment", back_populates="sessions")
