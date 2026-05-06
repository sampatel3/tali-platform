"""Add share_links table for the multi-link candidate report share contract.

HANDOFF v2 §3 — replaces the legacy
``CandidateApplication.report_share_token`` single-link with a proper
``share_links`` row per minted link, so recruiters can list active
links + revoke individual ones from the report footer + share modal.

Revision ID: 059_add_share_links
Revises: 058_add_anthropic_usage_reconciliation
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "059_add_share_links"
down_revision = "058_add_anthropic_usage_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Integer,
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("token", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry_preset", sa.String, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_share_links_token",
        "share_links",
        ["token"],
        unique=True,
    )
    op.create_index(
        "ix_share_links_application_id",
        "share_links",
        ["application_id"],
    )
    op.create_index(
        "ix_share_links_organization_id",
        "share_links",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_share_links_organization_id", table_name="share_links")
    op.drop_index("ix_share_links_application_id", table_name="share_links")
    op.drop_index("ix_share_links_token", table_name="share_links")
    op.drop_table("share_links")
