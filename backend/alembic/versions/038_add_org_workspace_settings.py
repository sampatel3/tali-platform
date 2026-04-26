"""Add workspace-level settings payloads for recruiter settings.

Revision ID: 038_add_org_workspace_settings
Revises: 037_merge_report_share_and_fireflies_heads
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "038_add_org_workspace_settings"
down_revision = "037_merge_report_share_and_fireflies_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("workspace_settings", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("scoring_policy", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("ai_tooling_config", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("notification_preferences", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "notification_preferences")
    op.drop_column("organizations", "ai_tooling_config")
    op.drop_column("organizations", "scoring_policy")
    op.drop_column("organizations", "workspace_settings")
