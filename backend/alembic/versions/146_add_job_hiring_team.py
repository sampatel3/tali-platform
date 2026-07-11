"""ATS slice B: per-job hiring team.

Additive: ``job_hiring_team`` — a user's membership on a specific job's hiring
team with a per-job role (hiring_manager / recruiter / interviewer /
coordinator). Distinct from the org-wide ``users.role``. Nothing enforces it
yet — it is the data model for per-job authorization + later scorecard work.

Revision ID: 146_add_job_hiring_team
Revises: 145_add_offers
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "146_add_job_hiring_team"
down_revision = "145_add_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_hiring_team",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "team_role", sa.String(), nullable=False, server_default="interviewer"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("role_id", "user_id", name="uq_job_hiring_team_role_user"),
    )
    op.create_index(
        "ix_job_hiring_team_organization_id", "job_hiring_team", ["organization_id"]
    )
    op.create_index("ix_job_hiring_team_role", "job_hiring_team", ["role_id"])
    op.create_index("ix_job_hiring_team_user", "job_hiring_team", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_job_hiring_team_user", table_name="job_hiring_team")
    op.drop_index("ix_job_hiring_team_role", table_name="job_hiring_team")
    op.drop_index("ix_job_hiring_team_organization_id", table_name="job_hiring_team")
    op.drop_table("job_hiring_team")
