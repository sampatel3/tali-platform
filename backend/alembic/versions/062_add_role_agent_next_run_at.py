"""Event-debounce window for the autonomous agent.

Adds ``roles.agent_next_run_at`` (nullable timestamp). Used to coalesce
bursts of application events into a single agent cycle: the first event
in a window claims the slot atomically and schedules the Celery task
with a countdown; subsequent events within the window no-op. The agent
task clears the field on entry so events arriving during the cycle
start a fresh window.

See app/agent_runtime/event_debounce.py.

Revision ID: 062_add_role_agent_next_run_at
Revises: 061_add_role_agent_model
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "062_add_role_agent_next_run_at"
down_revision = "061_add_role_agent_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "agent_next_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "agent_next_run_at")
