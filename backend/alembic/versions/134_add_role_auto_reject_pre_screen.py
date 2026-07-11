"""Per-role "Auto-reject pre-screen only" HITL toggle.

Narrower opt-in than ``auto_reject``: only candidates failing the cheap
pre-screen gate are rejected immediately (the ``run_auto_reject_if_needed``
path). Rejects of fully-scored candidates still queue as Decision Hub
cards. The full ``auto_reject`` toggle supersedes it (OR semantics).

Adds:
  * ``roles.auto_reject_pre_screen`` — Boolean, NOT NULL, server_default false.

Revision ID: 134_add_role_auto_reject_pre_screen
Revises: 133_add_role_auto_skip_assessment
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "134_add_role_auto_reject_pre_screen"
down_revision = "133_add_role_auto_skip_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "auto_reject_pre_screen",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "auto_reject_pre_screen")
