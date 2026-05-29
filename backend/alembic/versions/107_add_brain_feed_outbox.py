"""Add ``brain_feed_outbox`` — durable queue for the outbound mainspring feed.

Tali pushes anonymized learning signal (resolved decisions + their human
disposition, teach-loop outcomes, daily usage rollups) to mainspring's
cross-vertical brain. Producers (a periodic sweep) write rows here; a Celery
drain task ships pending rows to the mainspring ingest API with retry,
idempotent on ``event_id``. Sits behind MAINSPRING_BRAIN_FEED_ENABLED (default
off) so the live platform is unaffected until deliberately enabled.

Revision ID: 107_add_brain_feed_outbox
Revises: 106_add_cache_creation_1h_tokens
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "107_add_brain_feed_outbox"
down_revision = "106_add_cache_creation_1h_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brain_feed_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("record_kind", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
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
        "ix_brain_feed_outbox_event_id",
        "brain_feed_outbox",
        ["event_id"],
        unique=True,
    )
    # The drain task polls for pending rows; index status so it never table-scans.
    op.create_index(
        "ix_brain_feed_outbox_status",
        "brain_feed_outbox",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_brain_feed_outbox_status", table_name="brain_feed_outbox")
    op.drop_index("ix_brain_feed_outbox_event_id", table_name="brain_feed_outbox")
    op.drop_table("brain_feed_outbox")
