"""Tracks Postgres → Neo4j sync state per candidate.

A candidate row here means "we have at some point projected this
candidate into Neo4j". ``last_synced_at`` lets us compute drift
(candidates whose Postgres row changed after their last graph sync) and
trigger reconciliation. ``sync_version`` increments on every successful
sync — useful when investigating staleness.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.sql import func

from ..platform.database import Base


class GraphSyncState(Base):
    __tablename__ = "graph_sync_state"

    candidate_id = Column(
        Integer,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_synced_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sync_version = Column(Integer, nullable=False, default=0)
