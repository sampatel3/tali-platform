"""Persist the original ATS role on related-role requisition drafts.

Revision ID: 173_related_role_drafts
Revises: 172_workspace_agent_control
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "173_related_role_drafts"
down_revision = "172_workspace_agent_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_briefs",
        sa.Column("source_role_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_role_briefs_source_role_id",
        "role_briefs",
        ["source_role_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_role_briefs_source_role_id_roles",
        "role_briefs",
        "roles",
        ["source_role_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_role_briefs_source_role_id_roles",
        "role_briefs",
        type_="foreignkey",
    )
    op.drop_index("ix_role_briefs_source_role_id", table_name="role_briefs")
    op.drop_column("role_briefs", "source_role_id")
