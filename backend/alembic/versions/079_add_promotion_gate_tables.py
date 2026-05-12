"""Promotion-gate tables — Phase 5 of the multi-agent upgrade.

Three tables:

``bias_audit_results``
  One row per candidate ``policy_versions`` run through the audit.
  Holds per-segment selection / hire / calibration metrics plus
  pass/fail flags vs ``config/bias_audit_thresholds.yaml``.

``shadow_runs``
  One row per shadow-mode session. Tracks the candidate policy +
  the live policy it was compared against, the disagreement rate,
  and the realised-outcome accuracy delta. The promotion gate
  consults this row before flipping ``live``.

``gold_eval_examples``
  Manually-curated decisions with known correct outcomes. The
  promotion gate evaluates every candidate policy against this set
  before letting it through. Rows are seeded by hand or via a CSV
  import (out of scope for this migration).

Revision ID: 079_add_promotion_gate_tables
Revises: 078_add_policy_versions_and_exemplars
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "079_add_promotion_gate_tables"
down_revision = "078_add_policy_versions_and_exemplars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bias_audit_results",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "policy_version_id",
            sa.BigInteger(),
            sa.ForeignKey("policy_versions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        # JSON: {protected_attr: {segment: {selection_rate, hire_rate, ece, ...}}}
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        # JSON list of {threshold_name, observed, threshold, segment_a, segment_b}
        sa.Column("violations_json", sa.JSON(), nullable=True),
        sa.Column("override_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
    )

    op.create_table(
        "shadow_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "candidate_policy_version_id",
            sa.BigInteger(),
            sa.ForeignKey("policy_versions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "live_policy_version_id",
            sa.BigInteger(),
            sa.ForeignKey("policy_versions.id"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decisions_compared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disagreements", sa.Integer(), nullable=False, server_default="0"),
        # JSON: {candidate_correct, live_correct, candidate_log_loss, live_log_loss, ...}
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        # candidate | comparing | concluded
        sa.Column("status", sa.String(length=16), nullable=False, server_default="comparing"),
    )

    op.create_table(
        "gold_eval_examples",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("gold_eval_examples")
    op.drop_table("shadow_runs")
    op.drop_table("bias_audit_results")
