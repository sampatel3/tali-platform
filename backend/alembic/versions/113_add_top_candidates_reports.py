"""Add top_candidates_reports — shareable snapshots of a "top N with X" search.

A find_top_candidates result is persisted with an unguessable ``rpt_`` token so
a recruiter can share a read-only, no-auth report at /report/{token}.

Revision ID: 113_add_top_candidates_reports
Revises: 112_add_workable_provider
Create Date: 2026-06-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "113_add_top_candidates_reports"
down_revision = "112_add_workable_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "top_candidates_reports",
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
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("query", sa.String(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_top_candidates_reports_organization_id",
        "top_candidates_reports",
        ["organization_id"],
    )
    op.create_index(
        "ix_top_candidates_reports_token",
        "top_candidates_reports",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_top_candidates_reports_token", table_name="top_candidates_reports")
    op.drop_index(
        "ix_top_candidates_reports_organization_id",
        table_name="top_candidates_reports",
    )
    op.drop_table("top_candidates_reports")
