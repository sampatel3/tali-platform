"""Per-role "Auto skip assessment" HITL toggle.

When True the assessment stage is bypassed: a ``send_assessment`` verdict is
translated to ``advance_to_interview`` (same switch a role with no assessment
task gets), so strong candidates queue in the Decision Hub advance queue
instead of receiving an assessment invite.

Adds:
  * ``roles.auto_skip_assessment`` — Boolean, NOT NULL, server_default false.

Revision ID: 133_add_role_auto_skip_assessment
Revises: 132_per_application_workable_context
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "133_add_role_auto_skip_assessment"
down_revision = "132_per_application_workable_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "auto_skip_assessment",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "auto_skip_assessment")
