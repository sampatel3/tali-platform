"""``graph_episode_outbox`` — durable queue for Graphiti episode writes.

This table protects graph episodes that must match their Postgres source
transaction or survive a Graphiti outage. Hiring outcomes are irreplaceable
training signals; role-intent episodes must never outlive a rolled-back intent.

Successfully queued rows share the source transaction and therefore disappear
with its rollback. Role-intent enqueue defects are isolated in a child savepoint
so the optional graph mirror cannot discard the canonical recruiter answer. A
Celery drain task sends pending rows with retry/backoff; on failure the row stays
``pending`` for the next drain rather than vanishing.

``payload`` carries the keyword arguments needed to rebuild the episode at
drain time (see ``candidate_graph.episode_outbox``). ``dedup_key`` mirrors
the deterministic ``Episode.name`` so re-enqueuing the same episode is a
no-op — Graphiti itself also dedups by content, so a double-send is
harmless, but the unique key keeps the outbox itself clean.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# Episode kinds the outbox knows how to rebuild + dispatch.
EPISODE_KIND_HIRING_OUTCOME = "hiring_outcome"
EPISODE_KIND_DECISION = "decision"
EPISODE_KIND_RECRUITER_ACTION = "recruiter_action"
EPISODE_KIND_ROLE_INTENT = "role_intent"
GRAPH_EPISODE_KINDS = (
    EPISODE_KIND_HIRING_OUTCOME,
    EPISODE_KIND_DECISION,
    EPISODE_KIND_RECRUITER_ACTION,
    EPISODE_KIND_ROLE_INTENT,
)

# Row lifecycle.
OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_SENT = "sent"
OUTBOX_STATUS_FAILED = "failed"
GRAPH_OUTBOX_STATUSES = (
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_FAILED,
)


class GraphEpisodeOutbox(Base):
    __tablename__ = "graph_episode_outbox"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(
        Integer,
        ForeignKey(
            "roles.id",
            name="fk_graph_episode_outbox_role_id_roles",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    # One of GRAPH_EPISODE_KINDS — selects the builder at drain time.
    episode_kind = Column(String(32), nullable=False)
    # Deterministic per-episode identity (mirrors Episode.name) so a
    # re-enqueue of the same outcome doesn't create a duplicate row.
    dedup_key = Column(String(255), nullable=False, unique=True, index=True)
    # Builder kwargs, JSON-serialisable. Datetimes stored as ISO strings.
    payload = Column(JSON, nullable=False)

    status = Column(
        String(16), nullable=False, server_default=OUTBOX_STATUS_PENDING, index=True
    )
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "GraphEpisodeOutbox",
    "EPISODE_KIND_HIRING_OUTCOME",
    "EPISODE_KIND_DECISION",
    "EPISODE_KIND_RECRUITER_ACTION",
    "EPISODE_KIND_ROLE_INTENT",
    "GRAPH_EPISODE_KINDS",
    "OUTBOX_STATUS_PENDING",
    "OUTBOX_STATUS_SENT",
    "OUTBOX_STATUS_FAILED",
    "GRAPH_OUTBOX_STATUSES",
]
