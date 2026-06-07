"""Add graph_sync_state.content_hash — skip re-extracting unchanged candidates.

The Graphiti listeners fire on every Candidate AND CandidateApplication
update. An application stage change does not alter the candidate's profile
episodes, so without a content fingerprint a churning application re-runs the
full per-candidate Graphiti extraction (~N episodes, each several Haiku calls)
for zero graph delta. ``content_hash`` stores a fingerprint of the last
fully-synced episode set so ``sync_candidate`` can skip an unchanged re-sync.

Nullable + no backfill: a NULL hash means "fingerprint unknown", so the first
sync after deploy re-extracts once and records it.

Revision ID: 110_add_graph_sync_content_hash
Revises: 109_add_agent_conversations
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "110_add_graph_sync_content_hash"
down_revision = "109_add_agent_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_sync_state",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_sync_state", "content_hash")
