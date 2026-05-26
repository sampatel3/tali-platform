"""Add org-level agent kill switch columns to ``organizations``.

Mirrors the per-role pause pattern (``Role.agent_paused_at`` +
``Role.agent_paused_reason`` from the budget_guard precedent) one layer
up. When ``agent_paused_at`` is non-null on an organization, ``run_cycle``
short-circuits with ``status="kill_switched"`` BEFORE the first Anthropic
call, so the org-wide pause costs $0. The reason text surfaces in the
Hub banner and the per-candidate timeline.

Pairs with the env-only global kill switch (``settings.AGENT_KILL_SWITCH``)
— that's the platform-on-fire toggle; this one is the per-org operator
control.

Revision ID: 106_add_org_agent_kill_switch
Revises: 105_merge_104_heads
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "106_add_org_agent_kill_switch"
down_revision = "105_merge_104_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column("agent_paused_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("agent_paused_reason", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("agent_paused_reason")
        batch.drop_column("agent_paused_at")
