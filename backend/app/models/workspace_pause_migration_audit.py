"""Append-only evidence for the published workspace-pause conversion."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..platform.database import Base


_AUDIT_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class WorkspacePauseMigrationAudit(Base):
    """Durable, non-destructive evidence collected after immutable revision 175."""

    __tablename__ = "workspace_pause_migration_audits"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "migration_revision",
            name="uq_workspace_pause_migration_audits_org_revision",
        ),
        CheckConstraint(
            "converted_role_count >= 0",
            name="ck_workspace_pause_migration_audits_role_count",
        ),
        CheckConstraint(
            "control_version_before >= 1 AND control_version_after >= control_version_before",
            name="ck_workspace_pause_migration_audits_versions",
        ),
        Index(
            "ix_workspace_pause_migration_audits_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        _AUDIT_ID_TYPE,
        primary_key=True,
        autoincrement=True,
    )
    # Deliberately no FK: the compatibility evidence must outlive tenant/user
    # lifecycle operations and must never be cascade-deleted.
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    migration_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_quality: Mapped[str] = mapped_column(String(24), nullable=False)
    converted_role_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_role_event_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    source_role_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    source_workspace_event_id: Mapped[int | None] = mapped_column(
        _AUDIT_ID_TYPE,
        nullable=True,
        index=True,
    )
    recorded_workspace_event_id: Mapped[int | None] = mapped_column(
        _AUDIT_ID_TYPE,
        nullable=True,
        index=True,
    )
    compatibility_applied: Mapped[bool] = mapped_column(nullable=False)
    control_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    control_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    anomalies: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


__all__ = ["WorkspacePauseMigrationAudit"]
