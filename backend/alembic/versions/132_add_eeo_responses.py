"""P5: voluntary EEO / OFCCP self-identification (segregated).

Additive: ``eeo_responses`` — one voluntary self-ID per application, held apart
from the scoring graph. Only ever read in aggregate.

Revision ID: 132_add_eeo_responses
Revises: 131_add_data_subject_requests
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "132_add_eeo_responses"
down_revision = "131_add_data_subject_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eeo_responses",
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
        sa.Column("gender", sa.String(), nullable=True),
        sa.Column("race_ethnicity", sa.String(), nullable=True),
        sa.Column("veteran_status", sa.String(), nullable=True),
        sa.Column("disability_status", sa.String(), nullable=True),
        sa.Column("declined_to_answer", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("application_id", name="uq_eeo_response_application"),
    )


def downgrade() -> None:
    op.drop_table("eeo_responses")
