"""``graph_episode_outbox`` — durable queue for Graphiti episode writes.

Some Graphiti episodes are *irreplaceable* training signal: a
``HiringOutcome`` records what actually happened to a candidate after an
approved agent decision (interviewed / hired / rejected_confirmed). Unlike
a decision or a score — which can be re-derived from the Postgres
source-of-truth tables — a realised outcome cannot be reconstructed months
later. The original emit path was fire-and-forget (``emit_*`` swallows
every error), so a Graphiti outage silently dropped the one signal we can
never get back.

This table is the durable hop. Producers write a row here (atomically, in
the same transaction as the calibration write) instead of dispatching to
Graphiti inline. A Celery drain task then sends pending rows to Graphiti
with retry/backoff; on failure the row stays ``pending`` for the next
drain rather than vanishing.

``payload`` carries the keyword arguments needed to rebuild the episode at
drain time (see ``candidate_graph.episode_outbox``). ``dedup_key`` mirrors
the deterministic ``Episode.name`` so re-enqueuing the same outcome is a
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
GRAPH_EPISODE_KINDS = (EPISODE_KIND_HIRING_OUTCOME, EPISODE_KIND_DECISION)

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
    "GRAPH_EPISODE_KINDS",
    "OUTBOX_STATUS_PENDING",
    "OUTBOX_STATUS_SENT",
    "OUTBOX_STATUS_FAILED",
    "GRAPH_OUTBOX_STATUSES",
]
