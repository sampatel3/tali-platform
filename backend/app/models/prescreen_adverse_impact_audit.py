"""Persisted, aggregate-only adverse-impact audits for pre-screen decisions.

The source demographics live in the segregated voluntary self-identification
store. This row deliberately stores only small-cell-suppressed aggregate rates
and violations; it never stores application ids or per-person labels.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class PrescreenAdverseImpactAudit(Base):
    __tablename__ = "prescreen_adverse_impact_audits"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "window_start",
            "window_end",
            name="uq_prescreen_impact_audit_org_window",
        ),
        Index(
            "ix_prescreen_impact_audit_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(24), nullable=False)
    sample_size = Column(Integer, nullable=False, default=0)
    comparisons = Column(Integer, nullable=False, default=0)
    source = Column(String(32), nullable=False, default="voluntary_eeo")
    metrics_json = Column(JSON, nullable=False, default=dict)
    violations_json = Column(JSON, nullable=False, default=list)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["PrescreenAdverseImpactAudit"]
