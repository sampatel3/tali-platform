from sqlalchemy import (
    Boolean,
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

# Question answer types (mirrors Workable application_form question types).
QUESTION_KIND_TEXT = "text"
QUESTION_KIND_BOOLEAN = "boolean"
QUESTION_KIND_SINGLE_SELECT = "single_select"
QUESTION_KIND_MULTI_SELECT = "multi_select"
QUESTION_KIND_NUMBER = "number"
QUESTION_KINDS = (
    QUESTION_KIND_TEXT,
    QUESTION_KIND_BOOLEAN,
    QUESTION_KIND_SINGLE_SELECT,
    QUESTION_KIND_MULTI_SELECT,
    QUESTION_KIND_NUMBER,
)


class ScreeningQuestion(Base):
    """A per-role application-form question shown on the public apply form.

    Mirrors Workable's ``/jobs/:shortcode/application_form`` questions. A
    ``knockout`` question auto-fails the application when the answer isn't in
    ``knockout_expected`` (the deterministic pre-screen gate, run before any
    LLM). Candidate answers are stored on
    ``candidate_applications.screening_answers`` (a {question_id: answer} JSON
    map) rather than a join table. ``knockout`` / ``knockout_expected`` are
    NEVER exposed on the public careers surface (the passing answer must not
    leak to the applicant).
    """

    __tablename__ = "screening_questions"
    __table_args__ = (
        Index("ix_screening_questions_org_role", "organization_id", "role_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    prompt = Column(Text, nullable=False)
    kind = Column(String, nullable=False)
    # Choices for single/multi-select questions.
    options = Column(JSON, nullable=True)
    required = Column(Boolean, nullable=False, server_default="false")
    # When true, an answer not in ``knockout_expected`` auto-rejects the
    # application (deterministic gate, before any LLM).
    knockout = Column(Boolean, nullable=False, server_default="false")
    knockout_expected = Column(JSON, nullable=True)
    position = Column(Integer, nullable=False, server_default="0")
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization")
    role = relationship("Role")
