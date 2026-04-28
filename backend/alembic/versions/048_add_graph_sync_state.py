"""Add graph_sync_state to track Postgres -> Neo4j sync per candidate.

One row per candidate the graph sync has ever projected. Used to:
1. Detect drift (candidates updated in Postgres after their last graph sync).
2. Aid debugging when graph view differs from list view.

Revision ID: 048_add_graph_sync_state
Revises: 047_add_cv_embeddings
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "048_add_graph_sync_state"
down_revision = "047_add_cv_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_sync_state",
        sa.Column(
            "candidate_id",
            sa.Integer,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "sync_version",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_table("graph_sync_state")
