"""Append-only audit history for workspace-wide agent controls."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..platform.database import Base


_EVENT_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class WorkspaceAgentControlEvent(Base):
    __tablename__ = "workspace_agent_control_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('paused', 'resumed', 'migrated')",
            name="ck_workspace_agent_control_events_action",
        ),
        CheckConstraint(
            "from_version >= 1 AND to_version > from_version",
            name="ck_workspace_agent_control_events_version_advances",
        ),
        Index(
            "ix_workspace_agent_control_events_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        _EVENT_ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    # Retain the numeric tenant id even if an organization is later removed;
    # the audit history must not cascade away with its target.
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
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


__all__ = ["WorkspaceAgentControlEvent"]
