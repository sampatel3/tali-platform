"""Persist the Role revision accepted by each durable agent-chat turn.

Revision ID: 177_agent_chat_turn_role_version
Revises: 176_restore_application_timestamp_defaults
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "177_agent_chat_turn_role_version"
down_revision = "176_restore_application_timestamp_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("turn_accepted_role_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "turn_accepted_role_version")
