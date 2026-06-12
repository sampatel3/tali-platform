"""Add threshold_calibrations — learned role_fit advance/reject threshold.

A nightly job learns the score→advance/reject cut from recruiter terminal
decisions (Youden's J), bias-gates it, and writes a ``proposed`` row.
Activation (recruiter or opt-in bias-gated auto-apply) flips it to ``active``;
``resolve_role_fit_threshold`` reads the single active row per (org, role).
The raw role_fit score is never touched — only this policy boundary.

Revision ID: 114_add_threshold_calibrations
Revises: 113_add_top_candidates_reports
Create Date: 2026-06-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "114_add_threshold_calibrations"
down_revision = "113_add_top_candidates_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threshold_calibrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("learned_threshold", sa.Float(), nullable=False),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("n_positive", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_negative", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pooled_from_org", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("bias_gate_passed", sa.Boolean(), nullable=True),
        sa.Column("bias_gate_cold_start", sa.Boolean(), nullable=True),
        sa.Column("bias_gate_reason", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("training_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_threshold_calibrations_org_role_status",
        "threshold_calibrations",
        ["organization_id", "role_id", "status"],
    )
    op.create_index(
        "ix_threshold_calibrations_org_status",
        "threshold_calibrations",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_threshold_calibrations_org_status", table_name="threshold_calibrations"
    )
    op.drop_index(
        "ix_threshold_calibrations_org_role_status",
        table_name="threshold_calibrations",
    )
    op.drop_table("threshold_calibrations")
