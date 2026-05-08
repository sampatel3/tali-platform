"""``rubric_revisions`` — immutable history of rubric weight + threshold changes.

Every retune the agent applies (whether triggered by a feedback batch or a
manual admin edit) writes one row here. Each row links back to the
``decision_feedback`` rows that informed it via ``feedback_ids``, so the
Hub's SIGNAL section can show the cause-and-effect chain:

    decision_feedback (n rows) → rubric_revision (1 row) → next agent decisions

Old decisions are *not* re-scored — historical decisions stay tied to the
revision they were scored under, which is what makes the audit trail
meaningful.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


REVISION_CAUSES = (
    "human_edit",
    "feedback_retune",
    "manual_rollback",
)


class RubricRevision(Base):
    __tablename__ = "rubric_revisions"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)  # null = org-wide
    parent_revision_id = Column(BigInteger, ForeignKey("rubric_revisions.id"), nullable=True)

    cause = Column(String(32), nullable=False)
    # Stored as a JSON list of decision_feedback ids — JSON keeps the model
    # portable across Postgres + SQLite (used in tests). On Postgres the
    # column is JSON, not ARRAY, but lookups have always been
    # cardinality-low (≤20 ids per row) so we don't need the array ops.
    feedback_ids = Column(JSON, nullable=False, default=list)
    weights_diff = Column(JSON, nullable=True)
    threshold_diff = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    role = relationship("Role")
    parent = relationship("RubricRevision", remote_side=[id])
