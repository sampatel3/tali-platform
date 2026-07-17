"""Durable source-transaction intents for listener-driven graph ingestion.

Each row is immutable evidence that a committed Postgres mutation requested a
candidate, interview, or application-event refresh.  Delivery state lives on
the row so a broker outage, worker crash, or duplicate Celery message cannot
silently lose or multiply paid Graphiti work.
"""

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from ..platform.database import Base


GRAPH_INGEST_PENDING = "pending"
GRAPH_INGEST_DISPATCHING = "dispatching"
GRAPH_INGEST_QUEUED = "queued"
GRAPH_INGEST_CLAIMED = "claimed"
GRAPH_INGEST_PROVIDER_STARTED = "provider_started"
GRAPH_INGEST_COMPLETE = "complete"
GRAPH_INGEST_SKIPPED = "skipped"
GRAPH_INGEST_RECONCILIATION = "reconciliation_required"

GRAPH_INGEST_STATUSES = (
    GRAPH_INGEST_PENDING,
    GRAPH_INGEST_DISPATCHING,
    GRAPH_INGEST_QUEUED,
    GRAPH_INGEST_CLAIMED,
    GRAPH_INGEST_PROVIDER_STARTED,
    GRAPH_INGEST_COMPLETE,
    GRAPH_INGEST_SKIPPED,
    GRAPH_INGEST_RECONCILIATION,
)
GRAPH_INGEST_WORK_KINDS = ("candidate", "interview", "event")


class GraphIngestDispatch(Base):
    """One append-only graph-refresh intent created with its source write."""

    __tablename__ = "graph_ingest_dispatches"
    __table_args__ = (
        CheckConstraint(
            "work_kind IN ('candidate', 'interview', 'event')",
            name="ck_graph_ingest_dispatches_work_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatching', 'queued', 'claimed', "
            "'provider_started', 'complete', 'skipped', "
            "'reconciliation_required')",
            name="ck_graph_ingest_dispatches_status",
        ),
        CheckConstraint(
            "(operation_manifest IS NULL AND operation_manifest_sha256 IS NULL) "
            "OR (operation_manifest IS NOT NULL AND "
            "operation_manifest_sha256 IS NOT NULL)",
            name="ck_graph_ingest_dispatches_manifest_pair",
        ),
        Index(
            "ix_graph_ingest_dispatches_recovery",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_graph_ingest_dispatches_entity",
            "work_kind",
            "entity_id",
        ),
        Index(
            "ix_graph_ingest_dispatches_reconciliation",
            "organization_id",
            "status",
            "completed_at",
            "operation_id",
        ),
    )

    operation_id = Column(String(36), primary_key=True)
    organization_id = Column(Integer, nullable=True, index=True)
    work_kind = Column(String(16), nullable=False)
    entity_id = Column(Integer, nullable=False)
    # Every mapper mutation coalesced into this transaction-local refresh.
    # Values are secret-free ``{"kind": ..., "id": ...}`` identities.
    source_refs = Column(JSON, nullable=False)

    status = Column(
        String(32),
        nullable=False,
        default=GRAPH_INGEST_PENDING,
        server_default=GRAPH_INGEST_PENDING,
        index=True,
    )
    dispatch_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    dispatch_nonce = Column(String(36), nullable=True)
    worker_attempt_nonce = Column(String(36), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    provider_attempt_started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(128), nullable=True)
    # Immutable, bounded identity of the exact ordered episode payloads built
    # for this operation before its first provider call. Bodies remain in the
    # source tables; this manifest retains only safe names and SHA-256 digests.
    operation_manifest = Column(JSON(none_as_null=True), nullable=True)
    operation_manifest_sha256 = Column(String(64), nullable=True)
    # Append-only, secret-free snapshots of each owner-attested resolution.
    # Keeping this separate from ``source_refs`` preserves the immutable source
    # identities and lets a later ambiguous attempt retain every prior one.
    reconciliation_history = Column(JSON, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "GraphIngestDispatch",
    "GRAPH_INGEST_PENDING",
    "GRAPH_INGEST_DISPATCHING",
    "GRAPH_INGEST_QUEUED",
    "GRAPH_INGEST_CLAIMED",
    "GRAPH_INGEST_PROVIDER_STARTED",
    "GRAPH_INGEST_COMPLETE",
    "GRAPH_INGEST_SKIPPED",
    "GRAPH_INGEST_RECONCILIATION",
    "GRAPH_INGEST_STATUSES",
    "GRAPH_INGEST_WORK_KINDS",
]
