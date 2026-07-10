"""submittal_packs — curated multi-candidate client submittal share (WS2).

A role-scoped, ordered shortlist frozen at mint time and served read-only at
``GET /submittal/{token}``. Mirrors ``top_candidates_reports`` (token / expiry
/ revoke / view_count + a frozen JSON snapshot).

Revision ID: 138_add_submittal_packs
Revises: 137_add_interview_feedback
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "138_add_submittal_packs"
down_revision = "137_add_interview_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submittal_packs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "view_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_submittal_packs_organization_id",
        "submittal_packs",
        ["organization_id"],
    )
    op.create_index(
        "ix_submittal_packs_role_id",
        "submittal_packs",
        ["role_id"],
    )
    op.create_index(
        "ix_submittal_packs_token",
        "submittal_packs",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_submittal_packs_token", table_name="submittal_packs")
    op.drop_index("ix_submittal_packs_role_id", table_name="submittal_packs")
    op.drop_index(
        "ix_submittal_packs_organization_id", table_name="submittal_packs"
    )
    op.drop_table("submittal_packs")
