"""Add pre_screen_run_at to candidate_applications.

Tracks when the pre-screen LLM call last ran for an application. Used by:
1. The "Pre-screen new" batch action — skip applications whose CV has not
   changed since pre_screen_run_at (cv_uploaded_at <= pre_screen_run_at).
2. The candidate row UI — show "stale pre-screen" indicator when CV has
   been updated since the last pre-screen.
3. Idempotency for the cascade in batch-score: skip pre-screen step when
   the stored result is still valid.

Revision ID: 051_add_pre_screen_run_at
Revises: 050_merge_048_049_heads
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "051_add_pre_screen_run_at"
down_revision = "050_merge_048_049_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("pre_screen_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: if pre_screen_recommendation already exists, treat the run as
    # having happened at the existing scoring timestamp (best available proxy).
    op.execute(
        """
        UPDATE candidate_applications
        SET pre_screen_run_at = cv_match_scored_at
        WHERE pre_screen_recommendation IS NOT NULL
          AND cv_match_scored_at IS NOT NULL
          AND pre_screen_run_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "pre_screen_run_at")
