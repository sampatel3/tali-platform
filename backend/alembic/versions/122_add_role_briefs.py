"""Requisition: AI-native hiring brief (role_briefs).

Additive: a structured hiring brief attached to a (draft) role — job profile +
agent-context layers (success profile, priorities, dealbreakers, calibration
exemplars, sourcing signals, assessment focus, process). Captured via no-login
conversational intake; materializes onto the role + role_criterion.

Revision ID: 122_add_role_briefs
Revises: 121_add_pool_rescore_jobs
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "122_add_role_briefs"
down_revision = "121_add_pool_rescore_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_briefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("source_kind", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("department", sa.String(), nullable=True),
        sa.Column("location_city", sa.String(), nullable=True),
        sa.Column("location_country", sa.String(), nullable=True),
        sa.Column("workplace_type", sa.String(), nullable=True),
        sa.Column("employment_type", sa.String(), nullable=True),
        sa.Column("seniority", sa.String(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(), nullable=True),
        sa.Column("salary_period", sa.String(), nullable=True),
        sa.Column("openings", sa.Integer(), nullable=True),
        sa.Column("target_start", sa.String(), nullable=True),
        sa.Column("must_haves", sa.JSON(), nullable=True),
        sa.Column("preferred", sa.JSON(), nullable=True),
        sa.Column("dealbreakers", sa.JSON(), nullable=True),
        sa.Column("success_profile", sa.Text(), nullable=True),
        sa.Column("priorities", sa.JSON(), nullable=True),
        sa.Column("tradeoffs", sa.JSON(), nullable=True),
        sa.Column("calibration_exemplars", sa.JSON(), nullable=True),
        sa.Column("sourcing_signals", sa.JSON(), nullable=True),
        sa.Column("assessment_focus", sa.JSON(), nullable=True),
        sa.Column("process", sa.JSON(), nullable=True),
        sa.Column("evp", sa.JSON(), nullable=True),
        sa.Column("raw_input", sa.Text(), nullable=True),
        sa.Column("agent_state", sa.JSON(), nullable=True),
        sa.Column("completeness", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_role_briefs_id", "role_briefs", ["id"])
    op.create_index("ix_role_briefs_org_role", "role_briefs", ["organization_id", "role_id"])


def downgrade() -> None:
    op.drop_index("ix_role_briefs_org_role", table_name="role_briefs")
    op.drop_index("ix_role_briefs_id", table_name="role_briefs")
    op.drop_table("role_briefs")
