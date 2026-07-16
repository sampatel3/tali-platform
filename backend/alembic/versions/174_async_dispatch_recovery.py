"""Durable recovery receipts for user-triggered asynchronous work.

Revision ID: 174_async_dispatch_recovery
Revises: 173_reliable_integration_delivery
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "174_async_dispatch_recovery"
down_revision = "173_reliable_integration_delivery"
branch_labels = None
depends_on = None


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    _index("outreach_campaigns", "status")
    _index("outreach_messages", "status")
    _index("pool_rescore_jobs", "status")

    op.add_column("outreach_messages", sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outreach_messages", sa.Column("delivery_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outreach_messages", sa.Column("delivery_lease_until", sa.DateTime(timezone=True), nullable=True))
    _index("outreach_messages", "delivery_next_attempt_at")
    _index("outreach_messages", "delivery_lease_until")
    # A campaign accepted before this release has approved messages, while the
    # new sender only claims queued messages. Convert only campaigns already in
    # the sending state; draft/review approvals must remain recruiter-controlled.
    op.execute(
        sa.text(
            """
            UPDATE outreach_messages
               SET status = 'queued'
             WHERE status = 'approved'
               AND campaign_id IN (
                   SELECT id
                     FROM outreach_campaigns
                    WHERE status = 'sending'
               )
            """
        )
    )

    op.add_column("pool_rescore_jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("pool_rescore_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pool_rescore_jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pool_rescore_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pool_rescore_jobs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    _index("pool_rescore_jobs", "next_attempt_at")
    _index("pool_rescore_jobs", "lease_until")

    op.add_column("agent_conversations", sa.Column("turn_message_id", sa.Integer(), nullable=True))
    op.add_column("agent_conversations", sa.Column("turn_status", sa.String(length=24), nullable=True))
    op.add_column("agent_conversations", sa.Column("turn_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_conversations", sa.Column("turn_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_conversations", sa.Column("turn_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_conversations", sa.Column("turn_error", sa.String(length=500), nullable=True))
    for column in ("turn_message_id", "turn_status", "turn_next_attempt_at", "turn_lease_until"):
        _index("agent_conversations", column)

    op.add_column("agent_decisions", sa.Column("reevaluation_status", sa.String(length=24), nullable=True))
    op.add_column("agent_decisions", sa.Column("reevaluation_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_decisions", sa.Column("reevaluation_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_decisions", sa.Column("reevaluation_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_decisions", sa.Column("reevaluation_error", sa.String(length=500), nullable=True))
    for column in ("reevaluation_status", "reevaluation_next_attempt_at", "reevaluation_lease_until"):
        _index("agent_decisions", column)

    op.add_column("agent_runs", sa.Column("dispatch_key", sa.String(length=200), nullable=True))
    op.create_index("ix_agent_runs_dispatch_key", "agent_runs", ["dispatch_key"], unique=True)

    op.add_column(
        "background_job_runs",
        sa.Column("dispatch_key", sa.String(length=200), nullable=True),
    )
    op.create_index(
        "ix_background_job_runs_dispatch_key",
        "background_job_runs",
        ["dispatch_key"],
        unique=True,
    )

    op.create_table(
        "chat_command_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command_key", sa.String(length=96), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_kind", sa.String(length=24), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_command_receipts_command_key",
        "chat_command_receipts",
        ["command_key"],
        unique=True,
    )
    for column in ("organization_id", "role_id", "requested_by_user_id"):
        _index("chat_command_receipts", column)
    op.create_index(
        "ix_chat_command_receipts_conversation",
        "chat_command_receipts",
        ["conversation_kind", "conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_command_receipts_conversation",
        table_name="chat_command_receipts",
    )
    for column in ("requested_by_user_id", "role_id", "organization_id"):
        op.drop_index(
            f"ix_chat_command_receipts_{column}",
            table_name="chat_command_receipts",
        )
    op.drop_index(
        "ix_chat_command_receipts_command_key",
        table_name="chat_command_receipts",
    )
    op.drop_table("chat_command_receipts")

    op.drop_index(
        "ix_background_job_runs_dispatch_key", table_name="background_job_runs"
    )
    op.drop_column("background_job_runs", "dispatch_key")

    op.drop_index("ix_agent_runs_dispatch_key", table_name="agent_runs")
    op.drop_column("agent_runs", "dispatch_key")

    for column in ("reevaluation_lease_until", "reevaluation_next_attempt_at", "reevaluation_status"):
        op.drop_index(f"ix_agent_decisions_{column}", table_name="agent_decisions")
    for column in ("reevaluation_error", "reevaluation_lease_until", "reevaluation_next_attempt_at", "reevaluation_attempts", "reevaluation_status"):
        op.drop_column("agent_decisions", column)

    for column in ("turn_lease_until", "turn_next_attempt_at", "turn_status", "turn_message_id"):
        op.drop_index(f"ix_agent_conversations_{column}", table_name="agent_conversations")
    for column in ("turn_error", "turn_lease_until", "turn_next_attempt_at", "turn_attempts", "turn_status", "turn_message_id"):
        op.drop_column("agent_conversations", column)

    for column in ("lease_until", "next_attempt_at"):
        op.drop_index(f"ix_pool_rescore_jobs_{column}", table_name="pool_rescore_jobs")
    for column in ("updated_at", "started_at", "lease_until", "next_attempt_at", "attempts"):
        op.drop_column("pool_rescore_jobs", column)

    # The previous sender understands ``queued`` but not the leased ``sending``
    # state. Normalize in-flight rows before removing their lease metadata so a
    # rollback cannot strand an otherwise approved campaign permanently.
    op.execute(
        sa.text(
            """
            UPDATE outreach_messages
               SET status = 'queued',
                   delivery_lease_until = NULL,
                   delivery_next_attempt_at = NULL
             WHERE status = 'sending'
            """
        )
    )
    for column in ("delivery_lease_until", "delivery_next_attempt_at"):
        op.drop_index(f"ix_outreach_messages_{column}", table_name="outreach_messages")
    for column in ("delivery_lease_until", "delivery_next_attempt_at", "delivery_attempts"):
        op.drop_column("outreach_messages", column)

    op.drop_index("ix_pool_rescore_jobs_status", table_name="pool_rescore_jobs")
    op.drop_index("ix_outreach_messages_status", table_name="outreach_messages")
    op.drop_index("ix_outreach_campaigns_status", table_name="outreach_campaigns")
