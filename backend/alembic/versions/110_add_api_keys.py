"""Add api_keys — per-organization machine-to-machine API keys.

Backs the public API + Workable provider auth (docs/PUBLIC_API_BUILD_PLAN.md).
Only the SHA-256 hash of each secret is stored; the plaintext is shown once at
creation and never persisted.

Revision ID: 110_add_api_keys
Revises: 109_add_agent_conversations
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "110_add_api_keys"
down_revision = "109_add_agent_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column(
            "is_test", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("hashed_secret", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_api_keys_organization_id", "api_keys", ["organization_id"]
    )
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index(
        "ix_api_keys_hashed_secret", "api_keys", ["hashed_secret"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_hashed_secret", table_name="api_keys")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_organization_id", table_name="api_keys")
    op.drop_table("api_keys")
