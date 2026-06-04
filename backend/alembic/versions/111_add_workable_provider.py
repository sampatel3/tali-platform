"""Add the Workable Assessments-Provider surface.

Backs the marketplace add-on (docs/WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md):

- ``assessments.workable_callback_url``       — Workable's per-assessment results callback
- ``assessments.workable_provider_pushed_at`` — set once the completed result is enqueued
- ``organizations.workable_provider_config``  — per-org provider settings (callback auth token, …)
- ``workable_webhook_outbox``                 — durable queue for result callbacks to Workable

Revision ID: 111_add_workable_provider
Revises: 110_add_api_keys
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "111_add_workable_provider"
down_revision = "110_add_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("workable_callback_url", sa.String(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "workable_provider_pushed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("workable_provider_config", sa.JSON(), nullable=True),
    )

    op.create_table(
        "workable_webhook_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("callback_url", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workable_webhook_outbox_organization_id",
        "workable_webhook_outbox",
        ["organization_id"],
    )
    op.create_index(
        "ix_workable_webhook_outbox_status",
        "workable_webhook_outbox",
        ["status"],
    )
    op.create_index(
        "ix_workable_webhook_outbox_dedup_key",
        "workable_webhook_outbox",
        ["dedup_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workable_webhook_outbox_dedup_key",
        table_name="workable_webhook_outbox",
    )
    op.drop_index(
        "ix_workable_webhook_outbox_status",
        table_name="workable_webhook_outbox",
    )
    op.drop_index(
        "ix_workable_webhook_outbox_organization_id",
        table_name="workable_webhook_outbox",
    )
    op.drop_table("workable_webhook_outbox")
    op.drop_column("organizations", "workable_provider_config")
    op.drop_column("assessments", "workable_provider_pushed_at")
    op.drop_column("assessments", "workable_callback_url")
