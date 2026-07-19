"""Add assessment A/B experiment tables + per-assessment assignment columns.

Introduces ``assessment_experiments`` (one experiment scopes one role) and
``assessment_experiment_arms`` (the task/knob variants), plus columns on
``assessments`` that record which arm a candidate was assigned, the frozen knob
overrides, and a few scoring/runtime failure flags used by the hardening pass.

All ``assessments`` columns are nullable and safe on existing rows. Tests build
the schema from the models (``Base.metadata.create_all``), so this migration is
exercised only against Postgres (prod + throwaway-Postgres verification).

Revision ID: 104_add_assessment_experiments
Revises: 103_add_workable_stages_cache
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "104_add_assessment_experiments"
down_revision = "103_add_workable_stages_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("experiment_type", sa.String(), nullable=False, server_default="task"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("salt", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "key", name="uq_experiment_org_key"),
    )
    op.create_index(
        "ix_assessment_experiments_organization_id",
        "assessment_experiments",
        ["organization_id"],
    )
    op.create_index(
        "ix_assessment_experiments_role_id", "assessment_experiments", ["role_id"]
    )
    op.create_index(
        "ix_experiment_role_status", "assessment_experiments", ["role_id", "status"]
    )

    op.create_table(
        "assessment_experiment_arms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.Integer(),
            sa.ForeignKey("assessment_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("arm_key", sa.String(), nullable=False),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("knob_overrides", sa.JSON(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("experiment_id", "arm_key", name="uq_arm_experiment_key"),
    )
    op.create_index(
        "ix_assessment_experiment_arms_experiment_id",
        "assessment_experiment_arms",
        ["experiment_id"],
    )
    op.create_index(
        "ix_assessment_experiment_arms_task_id",
        "assessment_experiment_arms",
        ["task_id"],
    )
    op.create_index(
        "ix_arm_experiment_active",
        "assessment_experiment_arms",
        ["experiment_id", "is_active"],
    )

    # Per-assessment assignment record + knob overrides + failure flags.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("assessments") as batch_op:
            batch_op.add_column(
                sa.Column("experiment_id", sa.Integer(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("experiment_arm_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "assessments_experiment_id_fkey",
                "assessment_experiments",
                ["experiment_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                "assessments_experiment_arm_id_fkey",
                "assessment_experiment_arms",
                ["experiment_arm_id"],
                ["id"],
            )
    else:
        op.add_column(
            "assessments",
            sa.Column(
                "experiment_id",
                sa.Integer(),
                sa.ForeignKey("assessment_experiments.id"),
                nullable=True,
            ),
        )
        op.add_column(
            "assessments",
            sa.Column(
                "experiment_arm_id",
                sa.Integer(),
                sa.ForeignKey("assessment_experiment_arms.id"),
                nullable=True,
            ),
        )
    op.add_column("assessments", sa.Column("assignment_method", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("assignment_key", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("knob_variant_applied", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("score_weights_override", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("calibration_enabled", sa.Boolean(), nullable=True))
    op.add_column("assessments", sa.Column("scoring_failed", sa.Boolean(), nullable=True))
    op.add_column("assessments", sa.Column("scoring_partial", sa.Boolean(), nullable=True))
    op.add_column("assessments", sa.Column("repo_capture_failed", sa.Boolean(), nullable=True))
    op.add_column("assessments", sa.Column("test_parse_error", sa.Boolean(), nullable=True))
    op.create_index("ix_assessments_experiment_id", "assessments", ["experiment_id"])
    op.create_index(
        "ix_assessments_experiment_arm_id", "assessments", ["experiment_arm_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assessments_experiment_arm_id", table_name="assessments")
    op.drop_index("ix_assessments_experiment_id", table_name="assessments")
    op.drop_column("assessments", "test_parse_error")
    op.drop_column("assessments", "repo_capture_failed")
    op.drop_column("assessments", "scoring_partial")
    op.drop_column("assessments", "scoring_failed")
    op.drop_column("assessments", "calibration_enabled")
    op.drop_column("assessments", "score_weights_override")
    op.drop_column("assessments", "knob_variant_applied")
    op.drop_column("assessments", "assignment_key")
    op.drop_column("assessments", "assignment_method")
    op.drop_column("assessments", "experiment_arm_id")
    op.drop_column("assessments", "experiment_id")

    op.drop_index("ix_arm_experiment_active", table_name="assessment_experiment_arms")
    op.drop_index(
        "ix_assessment_experiment_arms_task_id", table_name="assessment_experiment_arms"
    )
    op.drop_index(
        "ix_assessment_experiment_arms_experiment_id",
        table_name="assessment_experiment_arms",
    )
    op.drop_table("assessment_experiment_arms")

    op.drop_index("ix_experiment_role_status", table_name="assessment_experiments")
    op.drop_index("ix_assessment_experiments_role_id", table_name="assessment_experiments")
    op.drop_index(
        "ix_assessment_experiments_organization_id", table_name="assessment_experiments"
    )
    op.drop_table("assessment_experiments")
