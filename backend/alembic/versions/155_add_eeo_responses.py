"""Voluntary EEO / OFCCP self-identification (segregated) + apply EEO token.

Additive:
- ``eeo_responses`` — one voluntary self-ID per application, held apart from the
  scoring graph. Only ever read in aggregate.
- ``candidate_applications.eeo_token`` — the opaque, single-purpose token the
  public EEO endpoint resolves (no raw application_id accepted from the public).

Revision ID: 155_add_eeo_responses
Revises: 154_add_screening_questions
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "155_add_eeo_responses"
down_revision = "154_add_screening_questions"
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
    op.add_column(
        "candidate_applications",
        sa.Column("eeo_token", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_candidate_applications_eeo_token",
        "candidate_applications",
        ["eeo_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_applications_eeo_token", table_name="candidate_applications"
    )
    op.drop_column("candidate_applications", "eeo_token")
    op.drop_table("eeo_responses")
