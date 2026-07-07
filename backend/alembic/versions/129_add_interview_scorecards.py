"""P3: structured interview scorecards.

Additive: ``interview_scorecards`` — one interviewer's structured feedback on an
application (recommendation + per-competency ratings + notes), optionally tied
to a linked ``application_interviews`` row.

Revision ID: 129_add_interview_scorecards
Revises: 128_add_offer_templates
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "129_add_interview_scorecards"
down_revision = "128_add_offer_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_scorecards",
        sa.Column("id", sa.Integer(), primary_key=True),
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
            "interview_id",
            sa.Integer(),
            sa.ForeignKey("application_interviews.id"),
            nullable=True,
        ),
        sa.Column(
            "interviewer_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("recommendation", sa.String(), nullable=True),
        sa.Column("overall_rating", sa.Integer(), nullable=True),
        sa.Column("competencies", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_interview_scorecards_application",
        "interview_scorecards",
        ["application_id"],
    )
    op.create_index(
        "ix_interview_scorecards_org", "interview_scorecards", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_interview_scorecards_org", table_name="interview_scorecards")
    op.drop_index(
        "ix_interview_scorecards_application", table_name="interview_scorecards"
    )
    op.drop_table("interview_scorecards")
