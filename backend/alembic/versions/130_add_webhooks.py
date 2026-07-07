"""P4: outbound webhooks.

Additive: ``webhook_subscriptions`` (an org's signed outbound endpoints) and
``webhook_deliveries`` (per-event attempt log / retry work-list).

Revision ID: 130_add_webhooks
Revises: 129_add_interview_scorecards
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "130_add_webhooks"
down_revision = "129_add_interview_scorecards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_subscriptions_org", "webhook_subscriptions", ["organization_id"]
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("webhook_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_deliveries_sub", "webhook_deliveries", ["subscription_id"]
    )
    op.create_index(
        "ix_webhook_deliveries_status", "webhook_deliveries", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_sub", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index(
        "ix_webhook_subscriptions_org", table_name="webhook_subscriptions"
    )
    op.drop_table("webhook_subscriptions")
