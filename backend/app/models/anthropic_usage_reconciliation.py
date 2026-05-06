"""Daily reconciliation rows comparing Anthropic billing to internal meter.

One row per (date, workspace_id, model). Populated by
``anthropic_reconciliation_service`` from a Celery beat task that runs
once a day for the prior 48h window (so late-arriving Anthropic data
catches up). Powers the "Reconciliation" panel in the settings → usage
tab — surfaces the % drift between what Anthropic billed us and what
the platform recorded as ``usage_events``.

Drift types we track:
- ``tokens_drift_pct`` — input + output tokens delta vs Anthropic
- ``cost_drift_pct``    — micro-USD delta vs Anthropic cost report

Negative drift (under-counted internally) is the dangerous direction:
spend Anthropic billed us for that we didn't attribute to a feature.
The UI flags any row > 1% drift.
"""
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class AnthropicUsageReconciliation(Base):
    __tablename__ = "anthropic_usage_reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    # ``usage_date`` is the start of the 1-day Anthropic bucket in UTC.
    # We always reconcile on UTC day boundaries to match Anthropic's
    # snapping behaviour.
    usage_date = Column(Date, nullable=False)
    # Anthropic workspace ID. ``null`` for the default workspace (the
    # shared key) — kept distinct from "no workspace" so we can detect
    # mis-routed traffic.
    anthropic_workspace_id = Column(String, nullable=True)
    # Resolved Tali organization, joined from ``organizations.anthropic_workspace_id``.
    # Null when Anthropic billed a workspace we don't recognise (drift in itself).
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    model = Column(String, nullable=False)

    # Anthropic-reported counters (source of truth)
    anthropic_input_tokens = Column(BigInteger, default=0, nullable=False)
    anthropic_output_tokens = Column(BigInteger, default=0, nullable=False)
    anthropic_cache_read_tokens = Column(BigInteger, default=0, nullable=False)
    anthropic_cache_creation_tokens = Column(BigInteger, default=0, nullable=False)
    # Cost in micro-USD (stored as BigInteger to match usage_events convention).
    # We convert from Anthropic's decimal-string cents at write time so
    # downstream comparisons with ``cost_usd_micro`` are integer-only.
    anthropic_cost_usd_micro = Column(BigInteger, default=0, nullable=False)

    # Internal counters aggregated from ``usage_events`` for the same window
    internal_input_tokens = Column(BigInteger, default=0, nullable=False)
    internal_output_tokens = Column(BigInteger, default=0, nullable=False)
    internal_cache_read_tokens = Column(BigInteger, default=0, nullable=False)
    internal_cache_creation_tokens = Column(BigInteger, default=0, nullable=False)
    internal_cost_usd_micro = Column(BigInteger, default=0, nullable=False)
    internal_event_count = Column(Integer, default=0, nullable=False)

    # Pre-computed drift percentages — written by the reconciliation task
    # so the UI doesn't recompute on every render. Numeric(7,3) gives us
    # +/- 9999.999% of headroom (we'll never see that much drift) with
    # 0.001% precision. Negative = internal under-counted vs Anthropic.
    tokens_drift_pct = Column(Numeric(7, 3), nullable=True)
    cost_drift_pct = Column(Numeric(7, 3), nullable=True)

    # Last time the reconciliation row was (re)written. Multiple runs in
    # a 24h window will overwrite as Anthropic data finalises.
    reconciled_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Free-form details (e.g. unrecognised workspace IDs, missing internal
    # rows) for debugging without requiring a schema migration.
    details = Column("metadata", JSON, nullable=True)

    organization = relationship("Organization")

    __table_args__ = (
        Index(
            "ix_anthropic_recon_date_workspace_model",
            "usage_date",
            "anthropic_workspace_id",
            "model",
            unique=True,
        ),
        Index("ix_anthropic_recon_org_date", "organization_id", "usage_date"),
        Index("ix_anthropic_recon_date", "usage_date"),
    )
