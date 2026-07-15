"""Add an idempotency source key for durable Agent Chat events.

Revision ID: 171_agent_chat_event_source_key
Revises: 170_merge_role_migration_heads
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "171_agent_chat_event_source_key"
down_revision = "170_merge_role_migration_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_conversation_messages") as batch:
        batch.add_column(sa.Column("source_key", sa.String(length=255), nullable=True))
        batch.create_unique_constraint(
            "uq_agent_conversation_messages_event_source",
            ["organization_id", "role_id", "source_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_conversation_messages") as batch:
        batch.drop_constraint(
            "uq_agent_conversation_messages_event_source",
            type_="unique",
        )
        batch.drop_column("source_key")
