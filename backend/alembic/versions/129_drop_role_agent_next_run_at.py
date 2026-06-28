"""Drop the dead ``roles.agent_next_run_at`` column.

The column was the write target of the event-debounce subsystem
(``agent_runtime/event_debounce.py`` + the ``agent_react_to_event`` task),
which was retired when the per-application event trigger was replaced by the
cohort-tick beat. With no writers left the column was always NULL, and its
only reader (the agent hub-panel "next run" display) has been removed.

Drops:
  * ``roles.agent_next_run_at`` — DateTime(timezone=True), nullable.

Revision ID: 129_drop_role_agent_next_run_at
Revises: 128_add_org_company_blurb
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "129_drop_role_agent_next_run_at"
down_revision = "128_add_org_company_blurb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("roles", "agent_next_run_at")


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("agent_next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
