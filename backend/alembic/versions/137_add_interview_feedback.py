"""interview_feedback — structured recruiter record of an interview result.

Joins a Taali score (via the application) to a human interview outcome so the
score↔outcome calibration script can measure predictive validity. ``role_id``
is denormalized for per-role reporting.

Revision ID: 136_add_interview_feedback
Revises: 135_add_anthropic_batch_jobs
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "137_add_interview_feedback"
down_revision = "136_add_assessment_preview_viewed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("candidate_applications.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=False,
        ),
        sa.Column(
            "interviewer_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("interviewer_name", sa.String(), nullable=True),
        sa.Column(
            "interview_round", sa.String(), nullable=False, server_default="interview"
        ),
        sa.Column("overall_recommendation", sa.String(), nullable=False),
        sa.Column("dimension_ratings", sa.JSON(), nullable=True),
        sa.Column("probe_results", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_interview_feedback_organization_id",
        "interview_feedback",
        ["organization_id"],
    )
    op.create_index(
        "ix_interview_feedback_application_id",
        "interview_feedback",
        ["application_id"],
    )
    op.create_index(
        "ix_interview_feedback_role_id",
        "interview_feedback",
        ["role_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interview_feedback_role_id", table_name="interview_feedback"
    )
    op.drop_index(
        "ix_interview_feedback_application_id", table_name="interview_feedback"
    )
    op.drop_index(
        "ix_interview_feedback_organization_id", table_name="interview_feedback"
    )
    op.drop_table("interview_feedback")
