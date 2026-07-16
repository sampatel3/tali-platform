"""Durable Fireflies inbox and leased integration outboxes.

Revision ID: 173_reliable_integration_delivery
Revises: 172_verify_legacy_active_owners
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "173_reliable_integration_delivery"
down_revision = "172_verify_legacy_active_owners"
branch_labels = None
depends_on = None


def _add_delivery_columns(table: str) -> None:
    op.add_column(table, sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index(f"ix_{table}_next_attempt_at", table, ["next_attempt_at"])
    op.create_index(f"ix_{table}_lease_until", table, ["lease_until"])


def upgrade() -> None:
    _add_delivery_columns("brain_feed_outbox")
    _add_delivery_columns("workable_webhook_outbox")

    op.create_table(
        "fireflies_webhook_inbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("meeting_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id",
            "meeting_id",
            "event_type",
            name="uq_fireflies_inbox_org_meeting_event",
        ),
    )
    for column in ("organization_id", "status", "next_attempt_at", "lease_until"):
        op.create_index(f"ix_fireflies_webhook_inbox_{column}", "fireflies_webhook_inbox", [column])

    # Preserve every historical interview (and its feedback FK) while keeping
    # the oldest row as the canonical provider link. Duplicate meeting ids were
    # produced by webhook retries; NULL remains legal for manual rows.
    op.execute(
        sa.text(
            """
            UPDATE application_interviews
            SET provider_meeting_id = NULL
            WHERE provider_meeting_id IS NOT NULL
              AND provider IS NOT NULL
              AND id NOT IN (
                SELECT MIN(id)
                FROM application_interviews
                WHERE provider_meeting_id IS NOT NULL
                  AND provider IS NOT NULL
                GROUP BY organization_id, provider, provider_meeting_id
              )
            """
        )
    )
    op.create_unique_constraint(
        "uq_application_interviews_provider_meeting",
        "application_interviews",
        ["organization_id", "provider", "provider_meeting_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_application_interviews_provider_meeting",
        "application_interviews",
        type_="unique",
    )
    for column in ("lease_until", "next_attempt_at", "status", "organization_id"):
        op.drop_index(f"ix_fireflies_webhook_inbox_{column}", table_name="fireflies_webhook_inbox")
    op.drop_table("fireflies_webhook_inbox")

    for table in ("workable_webhook_outbox", "brain_feed_outbox"):
        op.drop_index(f"ix_{table}_lease_until", table_name=table)
        op.drop_index(f"ix_{table}_next_attempt_at", table_name=table)
        op.drop_column(table, "lease_until")
        op.drop_column(table, "next_attempt_at")
