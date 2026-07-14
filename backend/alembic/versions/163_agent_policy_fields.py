"""Add granular role automation policy fields.

Revision ID: 163_agent_policy_fields
Revises: 162_invite_delivery_recovery
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "163_agent_policy_fields"
down_revision = "162_invite_delivery_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "auto_send_assessment",
        "auto_resend_assessment",
        "auto_advance",
    ):
        op.add_column("roles", sa.Column(name, sa.Boolean(), nullable=True))

    # Preserve every existing role's effective behavior. New clients may later
    # set the fields independently; NULL remains a supported legacy fallback.
    op.execute(
        sa.text(
            "UPDATE roles SET "
            "auto_send_assessment = auto_promote, "
            "auto_resend_assessment = auto_promote, "
            "auto_advance = auto_promote"
        )
    )


def downgrade() -> None:
    op.drop_column("roles", "auto_advance")
    op.drop_column("roles", "auto_resend_assessment")
    op.drop_column("roles", "auto_send_assessment")
