"""Recruiter overrides of cv_match_v3.0 recommendations.

Append-only audit log: a row is inserted each time a recruiter disagrees
with the LLM-derived recommendation. The model does not update existing
rows on re-score; downstream analytics deduplicate by ``(application_id,
created_at)``.

See migration ``043_add_cv_match_overrides.py``.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from ..platform.database import Base


class CvMatchOverride(Base):
    __tablename__ = "cv_match_overrides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(
        Integer,
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recruiter_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_trace_id = Column(String, nullable=True)
    original_recommendation = Column(String, nullable=True)
    override_recommendation = Column(String, nullable=False)
    original_score = Column(Float, nullable=True)
    recruiter_notes = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
