"""P1: screening questions (application-form / knockout questions).

Per-role ``screening_questions`` (mirrors Workable application_form questions) +
``candidate_applications.screening_answers`` ({question_id: answer}). Additive.

Revision ID: 125_add_screening_questions
Revises: 124_add_role_publish_fields
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "125_add_screening_questions"
down_revision = "124_add_role_publish_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screening_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("knockout", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("knockout_expected", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_screening_questions_id", "screening_questions", ["id"])
    op.create_index(
        "ix_screening_questions_org_role",
        "screening_questions",
        ["organization_id", "role_id"],
    )
    op.add_column(
        "candidate_applications",
        sa.Column("screening_answers", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "screening_answers")
    op.drop_index(
        "ix_screening_questions_org_role", table_name="screening_questions"
    )
    op.drop_index("ix_screening_questions_id", table_name="screening_questions")
    op.drop_table("screening_questions")
