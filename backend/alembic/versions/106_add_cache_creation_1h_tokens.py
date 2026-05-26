"""Split cache_creation tokens by TTL so 1-hour writes are priced correctly.

Anthropic's prompt-cache pricing differs by TTL:
- ephemeral_5m_input_tokens: 1.25× input rate
- ephemeral_1h_input_tokens: 2.00× input rate

Until 2026-05-26 the wrapper recorded a single combined
``cache_creation_tokens`` column and the pricing math always applied
1.25×. Since most cv_match / pre-screen / agent prompts use
``cache_control: {"type": "ephemeral", "ttl": "1h"}``, the 1-hour
writes were under-billed by 60% on that slice. The error was small in
absolute terms (the 2026-05-23 Haiku example was ~$0.41 of the $25
total gap) but it's a systematic bias and re-running with correct
math is straightforward.

This migration adds a NULLABLE ``cache_creation_1h_tokens`` column to
both tables. The wrapper populates it from
``response.usage.cache_creation.ephemeral_1h_input_tokens`` going
forward. NULL on historical rows is fine — pricing treats NULL as
"unknown 5m/1h split" and falls back to the 1.25× default for
backwards compatibility.

Revision ID: 106_add_cache_creation_1h_tokens
Revises: 105_merge_104_heads
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "106_add_cache_creation_1h_tokens"
down_revision = "105_merge_104_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claude_call_log",
        sa.Column("cache_creation_1h_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_events",
        sa.Column("cache_creation_1h_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_events", "cache_creation_1h_tokens")
    op.drop_column("claude_call_log", "cache_creation_1h_tokens")
