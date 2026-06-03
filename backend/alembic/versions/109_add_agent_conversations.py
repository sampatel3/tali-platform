"""Add agent-conversation tables — "chat to the role's agent".

Three tables backing the conversational agent surface on the Home hub:

* ``agent_conversations`` — one thread per (organization, role); shared
  across the org's recruiters (the role *is* the agent).
* ``agent_conversation_messages`` — Anthropic-format message log (raw block
  ``content`` for replay + flattened ``text`` / structured ``actions`` for
  the rendered timeline).
* ``agent_conversation_reads`` — per-user last-read marker driving the
  unread badge on each sidebar agent.

No backfill: conversations are created lazily the first time a recruiter
opens a role's agent.

Revision ID: 109_add_agent_conversations
Revises: 108_add_brain_feed_outbox
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "109_add_agent_conversations"
down_revision = "108_add_brain_feed_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "role_id", name="uq_agent_conversations_org_role"
        ),
    )
    op.create_index(
        "ix_agent_conversations_organization_id",
        "agent_conversations",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_conversations_role_id", "agent_conversations", ["role_id"]
    )

    op.create_table(
        "agent_conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False
        ),
        sa.Column("author_role", sa.String(), nullable=False),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default="chat"),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_agent_conversation_messages_conversation_id",
        "agent_conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_messages_organization_id",
        "agent_conversation_messages",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_conversation_messages_role_id",
        "agent_conversation_messages",
        ["role_id"],
    )

    op.create_table(
        "agent_conversation_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_agent_conversation_reads_convo_user",
        ),
    )
    op.create_index(
        "ix_agent_conversation_reads_conversation_id",
        "agent_conversation_reads",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_reads_user_id",
        "agent_conversation_reads",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_conversation_reads_user_id",
        table_name="agent_conversation_reads",
    )
    op.drop_index(
        "ix_agent_conversation_reads_conversation_id",
        table_name="agent_conversation_reads",
    )
    op.drop_table("agent_conversation_reads")

    op.drop_index(
        "ix_agent_conversation_messages_role_id",
        table_name="agent_conversation_messages",
    )
    op.drop_index(
        "ix_agent_conversation_messages_organization_id",
        table_name="agent_conversation_messages",
    )
    op.drop_index(
        "ix_agent_conversation_messages_conversation_id",
        table_name="agent_conversation_messages",
    )
    op.drop_table("agent_conversation_messages")

    op.drop_index(
        "ix_agent_conversations_role_id", table_name="agent_conversations"
    )
    op.drop_index(
        "ix_agent_conversations_organization_id",
        table_name="agent_conversations",
    )
    op.drop_table("agent_conversations")
