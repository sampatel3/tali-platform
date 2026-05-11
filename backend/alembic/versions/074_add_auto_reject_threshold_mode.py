"""Add ``auto_reject_threshold_mode`` to roles.

Lets recruiters pick whether the role's pre-screen reject threshold is
``manual`` (a number they tune themselves) or ``auto`` (the agent
computes a recommendation from the role's score distribution + any
advance/hire labels). Defaults to ``manual`` so existing roles keep
behaving exactly as they did before this migration.

Revision ID: 074_add_auto_reject_threshold_mode
Revises: 073_inject_pre_screen_auto_reject_rule
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "074_add_auto_reject_threshold_mode"
down_revision = "073_inject_pre_screen_auto_reject_rule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "auto_reject_threshold_mode",
            sa.String(length=8),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "auto_reject_threshold_mode")
