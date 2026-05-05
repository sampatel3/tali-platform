"""Add taali_chat_conversations + taali_chat_messages.

Stores recruiter ↔ Taali Chat transcripts. Org-scoped, soft-delete via
``archived_at``. ``content`` carries Anthropic-shaped content blocks
(text / tool_use / tool_result) so a message list can be replayed back
to ``messages.create`` on follow-up turns without re-shaping.

Revision ID: 055_add_taali_chat_tables
Revises: 054_merge_052_heads
Create Date: 2026-05-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "055_add_taali_chat_tables"
down_revision = "054_merge_052_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "taali_chat_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_taali_chat_conversations_org_user_recent",
        "taali_chat_conversations",
        ["organization_id", "user_id", "created_at"],
    )

    op.create_table(
        "taali_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("taali_chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_taali_chat_messages_conversation_created",
        "taali_chat_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taali_chat_messages_conversation_created", table_name="taali_chat_messages"
    )
    op.drop_table("taali_chat_messages")
    op.drop_index(
        "ix_taali_chat_conversations_org_user_recent",
        table_name="taali_chat_conversations",
    )
    op.drop_table("taali_chat_conversations")
