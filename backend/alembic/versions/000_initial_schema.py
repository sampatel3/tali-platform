"""Create the exact pre-Alembic core schema.

This is the canonical baseline reconstructed from git revision ``612a672``,
the parent of the commit that introduced migration 001.  Keeping the historic
shape here lets a genuinely empty database traverse every later migration,
including PostgreSQL-only triggers, indexes, constraints, and data invariants.

Revision ID: 000_initial_schema
Revises:
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "000_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


_ASSESSMENT_STATUS = sa.Enum(
    "PENDING",
    "IN_PROGRESS",
    "COMPLETED",
    "EXPIRED",
    name="assessmentstatus",
)


def upgrade() -> None:
    # Alembic creates its version table as VARCHAR(32) before the first
    # revision. This repository's historical revision ids became longer than
    # that at revision 015, so widen the bookkeeping column while the database
    # is still at the baseline. Existing deployed databases necessarily
    # already support those ids and never execute this ancestor.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("workable_subdomain", sa.String(), nullable=True),
        sa.Column("workable_access_token", sa.String(), nullable=True),
        sa.Column("workable_refresh_token", sa.String(), nullable=True),
        sa.Column("workable_connected", sa.Boolean(), nullable=True),
        sa.Column("workable_config", sa.JSON(), nullable=True),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("plan", sa.String(), nullable=True),
        sa.Column("assessments_used", sa.Integer(), nullable=True),
        sa.Column("assessments_limit", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organizations_id", "organizations", ["id"])
    op.create_index(
        "ix_organizations_slug", "organizations", ["slug"], unique=True
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("workable_candidate_id", sa.String(), nullable=True),
        sa.Column("workable_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"])
    op.create_index("ix_candidates_id", "candidates", ["id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=True),
        sa.Column("difficulty", sa.String(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("starter_code", sa.Text(), nullable=True),
        sa.Column("test_code", sa.Text(), nullable=True),
        sa.Column("sample_data", sa.JSON(), nullable=True),
        sa.Column("dependencies", sa.JSON(), nullable=True),
        sa.Column("success_criteria", sa.JSON(), nullable=True),
        sa.Column("test_weights", sa.JSON(), nullable=True),
        sa.Column("is_template", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_id", "tasks", ["id"])

    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("token", sa.String(), nullable=True),
        sa.Column("status", _ASSESSMENT_STATUS, nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("tests_passed", sa.Integer(), nullable=True),
        sa.Column("tests_total", sa.Integer(), nullable=True),
        sa.Column("code_quality_score", sa.Float(), nullable=True),
        sa.Column("time_efficiency_score", sa.Float(), nullable=True),
        sa.Column("ai_usage_score", sa.Float(), nullable=True),
        sa.Column("test_results", sa.JSON(), nullable=True),
        sa.Column("ai_prompts", sa.JSON(), nullable=True),
        sa.Column("code_snapshots", sa.JSON(), nullable=True),
        sa.Column("timeline", sa.JSON(), nullable=True),
        sa.Column("e2b_session_id", sa.String(), nullable=True),
        sa.Column("workable_candidate_id", sa.String(), nullable=True),
        sa.Column("workable_job_id", sa.String(), nullable=True),
        sa.Column("posted_to_workable", sa.Boolean(), nullable=True),
        sa.Column(
            "posted_to_workable_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assessments_id", "assessments", ["id"])
    op.create_index(
        "ix_assessments_token", "assessments", ["token"], unique=True
    )

    op.create_table(
        "assessment_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=True),
        sa.Column(
            "session_start",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("session_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("keystrokes", sa.Integer(), nullable=True),
        sa.Column("code_executions", sa.Integer(), nullable=True),
        sa.Column("ai_requests", sa.Integer(), nullable=True),
        sa.Column("activity_log", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_sessions_id", "assessment_sessions", ["id"]
    )


def downgrade() -> None:
    op.drop_table("assessment_sessions")
    op.drop_table("assessments")
    op.drop_table("tasks")
    op.drop_table("candidates")
    op.drop_table("users")
    op.drop_table("organizations")
    if op.get_bind().dialect.name == "postgresql":
        _ASSESSMENT_STATUS.drop(op.get_bind(), checkfirst=True)
