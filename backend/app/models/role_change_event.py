"""Append-only audit records for shared ``Role`` configuration changes.

The database migration owns the physical append-only trigger.  This model is
deliberately small and immutable-by-convention so every writer can add the
event in the same transaction as the role mutation it describes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..platform.database import Base


# SQLite only auto-increments a column declared exactly ``INTEGER PRIMARY
# KEY``.  Production Postgres still gets BIGINT, while tests retain normal
# auto-increment behaviour without a model event-listener workaround.
_AUDIT_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class RoleChangeEvent(Base):
    """One versioned, tenant-scoped change to a shared role.

    ``changes`` contains only the bounded/redacted output produced by
    :mod:`app.services.role_change_audit`; callers must not persist raw request
    bodies in this column.  Actor deletion preserves the audit row by setting
    ``actor_user_id`` to NULL.
    """

    __tablename__ = "role_change_events"
    __table_args__ = (
        CheckConstraint(
            "from_version >= 0",
            name="ck_role_change_events_from_version_nonnegative",
        ),
        CheckConstraint(
            "to_version > from_version",
            name="ck_role_change_events_version_advances",
        ),
        Index(
            "ix_role_change_events_org_role_created",
            "organization_id",
            "role_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        _AUDIT_ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    organization_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    # Retain the historical tenant id too. Cascading an organization delete
    # would conflict with the database's append-only guarantee.
    # Deliberately not a foreign key: a hard-deleted Role must not cascade away
    # its append-only history.  Tenant scoping plus the captured numeric id keep
    # the historical resource identity queryable after deletion.
    role_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
