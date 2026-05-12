"""``capability_flags`` — hot-reloaded source of truth for v10 capability rollouts.

Layout matches ``capability_flags_addendum.md`` §2. PK is
(capability, organization_id) where organization_id NULL is the global
default. Org-scoped overrides shadow the global default.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class CapabilityFlag(Base):
    __tablename__ = "capability_flags"

    capability = Column(String(64), primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), primary_key=True, nullable=True
    )
    enabled = Column(Boolean, nullable=False, server_default=false())
    scope_json = Column(JSON, nullable=False)
    requires_json = Column(JSON, nullable=False, server_default="[]")
    rolled_out_by = Column(String(128), nullable=False)
    rolled_out_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rollback_reason = Column(Text, nullable=True)
