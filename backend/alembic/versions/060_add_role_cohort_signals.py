"""Cache cohort signals on the role row.

Adds two nullable columns to ``roles``:
- ``agent_cohort_signals`` (JSON): the "do high scorers cluster" payload
  produced by ``cohort_signals_service.compute_cohort_signals`` — feature
  lifts across skills / companies / titles / schools for the top decile
  of scored applicants vs the full pool.
- ``agent_cohort_signals_at`` (timestamp): when the cache was populated.
  The agent's get_cohort_signals tool refreshes when stale (> 1 hour).

Revision ID: 060_add_role_cohort_signals
Revises: 059_add_share_links
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "060_add_role_cohort_signals"
down_revision = "059_add_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("agent_cohort_signals", sa.JSON(), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column(
            "agent_cohort_signals_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "agent_cohort_signals_at")
    op.drop_column("roles", "agent_cohort_signals")
