"""Cache Workable job stages on roles.

The stage pickers (home review queue + jobs candidate drawer) used to call the
Workable ``/jobs/:shortcode/stages`` API live on every modal open. That live
call carries a fixed throttle and an 11s wait-then-retry on rate-limit, so the
picker felt slow even though the data barely changes. These columns let us
store the pipeline once during sync and serve it instantly from our own DB.

Revision ID: 103_add_workable_stages_cache
Revises: 102_add_genuine_pre_screen_score
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "103_add_workable_stages_cache"
down_revision = "102_add_genuine_pre_screen_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("workable_stages", sa.JSON(), nullable=True))
    op.add_column(
        "roles",
        sa.Column("workable_stages_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "workable_stages_synced_at")
    op.drop_column("roles", "workable_stages")
