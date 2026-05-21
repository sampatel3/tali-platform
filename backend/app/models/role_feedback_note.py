"""``role_feedback_notes`` — freeform recruiter feedback on a role.

Append-only timeline. The recruiter writes a note when they notice a
pattern across decisions (e.g. "the agent keeps over-rejecting people
with non-traditional backgrounds"); the most-recent N notes are
inlined into the agent's system prompt so the agent picks the feedback
up on the next cycle without any structured-intent edit.

Distinct from:
- ``decision_feedback`` — scoped to a single agent decision.
- ``role_intents`` — manually-curated structured overlay, versioned.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class RoleFeedbackNote(Base):
    __tablename__ = "role_feedback_notes"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    role = relationship("Role")
    author = relationship("User", foreign_keys=[author_user_id])
