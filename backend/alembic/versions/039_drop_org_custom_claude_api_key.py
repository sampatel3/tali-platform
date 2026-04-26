"""Drop the per-org custom Claude API key columns.

Customers do not bring their own Anthropic key on tali-platform. All Claude
usage goes through Taali's settings.ANTHROPIC_API_KEY. The per-org plumbing
introduced in 020_add_assessment_terminal_cli_fields is no longer reachable
from the application after this migration.

Revision ID: 039_drop_org_custom_claude_api_key
Revises: 038_add_org_workspace_settings
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "039_drop_org_custom_claude_api_key"
down_revision = "038_add_org_workspace_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("claude_api_key_last_rotated_at")
        batch.drop_column("claude_api_key_encrypted")


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("claude_api_key_encrypted", sa.String(), nullable=True))
        batch.add_column(sa.Column("claude_api_key_last_rotated_at", sa.DateTime(timezone=True), nullable=True))
