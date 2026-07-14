"""Persist scoring execution authority and full-score intent.

Revision ID: 165_score_job_authority
Revises: 164_merge_agent_search_heads
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "165_score_job_authority"
down_revision = "164_merge_agent_search_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cv_score_jobs",
        sa.Column(
            "requires_active_agent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "cv_score_jobs",
        sa.Column(
            "force_full_score",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Enabled legacy rows must never interpret a missing/zero cap as
    # unlimited.  New activation already requires a positive cap; normalize
    # only the historical impossible state to the documented $50 fallback.
    op.execute(
        sa.text(
            "UPDATE roles SET monthly_usd_budget_cents = 5000 "
            "WHERE agentic_mode_enabled IS TRUE "
            "AND (monthly_usd_budget_cents IS NULL "
            "OR monthly_usd_budget_cents <= 0)"
        )
    )


def downgrade() -> None:
    op.drop_column("cv_score_jobs", "force_full_score")
    op.drop_column("cv_score_jobs", "requires_active_agent")
