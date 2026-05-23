"""``capability_flags`` — hot-reloaded source of truth for v10 capability rollouts.

Layout matches ``capability_flags_addendum.md`` §2. A surrogate ``id`` is
the primary key; uniqueness is enforced by two *partial* unique indexes:

* ``uq_capability_flags_global`` — one global default per capability
  (``organization_id IS NULL``).
* ``uq_capability_flags_org`` — one override per
  (capability, organization_id) for org-scoped rows.

The original composite PK on ``(capability, organization_id)`` forced
``organization_id`` NOT NULL on Postgres (PK columns can't be null), which
made the global (``organization_id IS NULL``) rows impossible to insert.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class CapabilityFlag(Base):
    __tablename__ = "capability_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    capability = Column(String(64), nullable=False)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    enabled = Column(Boolean, nullable=False, server_default=false())
    scope_json = Column(JSON, nullable=False)
    requires_json = Column(JSON, nullable=False, server_default="[]")
    rolled_out_by = Column(String(128), nullable=False)
    rolled_out_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rollback_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_capability_flags_global",
            "capability",
            unique=True,
            sqlite_where=text("organization_id IS NULL"),
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index(
            "uq_capability_flags_org",
            "capability",
            "organization_id",
            unique=True,
            sqlite_where=text("organization_id IS NOT NULL"),
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index("ix_capability_flags_by_org", "organization_id"),
    )
