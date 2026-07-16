"""Give related roles their own Taali candidate workflow state.

Revision ID: 174_related_role_workflow
Revises: 173_related_role_drafts
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "174_related_role_workflow"
down_revision = "173_related_role_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "pipeline_stage",
            sa.String(length=32),
            nullable=False,
            server_default="applied",
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "pipeline_stage_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "pipeline_stage_source",
            sa.String(length=16),
            nullable=False,
            server_default="system",
        ),
    )
    op.create_index(
        "ix_sister_evaluations_role_pipeline_stage",
        "sister_role_evaluations",
        ["role_id", "pipeline_stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sister_evaluations_role_pipeline_stage",
        table_name="sister_role_evaluations",
    )
    op.drop_column("sister_role_evaluations", "pipeline_stage_source")
    op.drop_column("sister_role_evaluations", "pipeline_stage_updated_at")
    op.drop_column("sister_role_evaluations", "pipeline_stage")
