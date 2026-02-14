"""Add demo lead + marketing fields for candidates and assessments.

Revision ID: 018_add_demo_lead_fields
Revises: 017_add_application_cv_match_fields
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa


revision = "018_add_demo_lead_fields"
down_revision = "017_add_application_cv_match_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("work_email", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("company_name", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("company_size", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("lead_source", sa.String(), nullable=True))
    op.add_column(
        "candidates",
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_candidates_work_email", "candidates", ["work_email"])

    op.add_column(
        "assessments",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("assessments", sa.Column("demo_track", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("demo_profile", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "demo_profile")
    op.drop_column("assessments", "demo_track")
    op.drop_column("assessments", "is_demo")

    op.drop_index("ix_candidates_work_email", table_name="candidates")
    op.drop_column("candidates", "marketing_consent")
    op.drop_column("candidates", "lead_source")
    op.drop_column("candidates", "company_size")
    op.drop_column("candidates", "company_name")
    op.drop_column("candidates", "work_email")
