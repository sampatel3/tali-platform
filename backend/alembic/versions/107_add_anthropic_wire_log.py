"""Ground-truth wire log of every Anthropic /v1/messages HTTP request.

Why this exists: reconciliation against Anthropic billing kept showing
residual Haiku drift (~8-15%) that we couldn't fully attribute. Every
investigation reasoned about *which functions should* go through the
metering wrapper, instead of *measuring which HTTP requests actually
left the process*. This table is the measurement.

A wire-tap (``services/anthropic_wire_tap``) patches ``httpx.Client.send``
and ``httpx.AsyncClient.send`` and writes one row here per outbound
request to ``api.anthropic.com/v1/messages*`` — regardless of which
client made it (wrapped, bare, Graphiti, the gateway), and including
SDK-internal retries (each retry is a separate send()).

Diff this against ``claude_call_log`` on ``anthropic_request_id``:
wire rows with no matching call_log row = a metering bypass, located
exactly. It's deliberately minimal (no token columns) — headers/status
only, so the hook never reads the response body (safe for streaming).

Revision ID: 107_add_anthropic_wire_log
Revises: 106_add_cache_creation_1h_tokens
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "107_add_anthropic_wire_log"
down_revision = "106_add_cache_creation_1h_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anthropic_wire_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("anthropic_request_id", sa.String(), nullable=True),
        sa.Column("path", sa.String(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(), nullable=True),
        sa.Column("is_stream", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_anthropic_wire_log_created", "anthropic_wire_log", ["created_at"]
    )
    op.create_index(
        "ix_anthropic_wire_log_request_id",
        "anthropic_wire_log",
        ["anthropic_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_anthropic_wire_log_request_id", table_name="anthropic_wire_log")
    op.drop_index("ix_anthropic_wire_log_created", table_name="anthropic_wire_log")
    op.drop_table("anthropic_wire_log")
