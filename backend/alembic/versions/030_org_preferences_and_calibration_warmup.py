"""Add org preference fields and assessment calibration warmup prompt.

Revision ID: 030_org_preferences_and_calibration_warmup
Revises: 029_candidate_feedback_and_debrief
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


revision = "030_org_preferences_and_calibration_warmup"
down_revision = "029_candidate_feedback_and_debrief"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "default_assessment_duration_minutes",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column("organizations", sa.Column("invite_email_template", sa.Text(), nullable=True))
    op.add_column("assessments", sa.Column("calibration_warmup_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "calibration_warmup_prompt")
    op.drop_column("organizations", "invite_email_template")
    op.drop_column("organizations", "default_assessment_duration_minutes")
