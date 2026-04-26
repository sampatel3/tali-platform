"""Add internal candidate report share links.

Revision ID: 036_add_application_report_share_links
Revises: 035_workable_first_hiring_intelligence
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "036_add_application_report_share_links"
down_revision = "035_workable_first_hiring_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("report_share_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("report_share_created_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "ix_candidate_applications_report_share_token",
        "candidate_applications",
        ["report_share_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_applications_report_share_token", table_name="candidate_applications")

    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("report_share_created_at")
        batch.drop_column("report_share_token")
