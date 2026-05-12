"""``role_intents`` — manually authored, versioned recruiter intent.

See ``amendment_a1_recruiter_intent.md`` §A1.3-A1.5. One row per
(role, version). Lookup the *active* row for a role at time ``t`` via
``role_intent.fetch_active_intent``.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class RoleIntent(Base):
    __tablename__ = "role_intents"
    __table_args__ = (
        UniqueConstraint("role_id", "version", name="uq_role_intents_role_version"),
    )

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    structured_fields = Column(JSON, nullable=False)
    free_text = Column(Text, nullable=True)
    superseded_id = Column(
        BigInteger,
        ForeignKey(
            "role_intents.id",
            use_alter=True,
            name="fk_role_intents_superseded_id",
        ),
        nullable=True,
    )
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    authored_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    authored_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    role = relationship("Role")
    organization = relationship("Organization")
    authored_by = relationship("User", foreign_keys=[authored_by_user_id])
    superseded = relationship(
        "RoleIntent",
        remote_side=[id],
        foreign_keys=[superseded_id],
    )
