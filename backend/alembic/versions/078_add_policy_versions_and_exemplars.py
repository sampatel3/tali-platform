"""``policy_versions`` and ``agent_exemplars`` — Phase 3 of the multi-agent upgrade.

``policy_versions`` is the canonical home for *fitted* policy models —
distinct from ``decision_policies`` (which holds the rule-driven
verdict policy bootstrap and overlay). Each row is a candidate
fitted model produced by ``nightly_policy_fit`` and gated by the
Phase 5 promotion gate.

``agent_exemplars`` is per-sub-agent — one row per teach event that
attributed to that agent, retained up to a cap (D4 = 500 per agent
per org per role). The retrieval path pulls top-k by feature
similarity at score time and injects them as few-shot.

Revision ID: 078_add_policy_versions_and_exemplars
Revises: 077_add_attributed_feedback_columns
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "078_add_policy_versions_and_exemplars"
down_revision = "077_add_attributed_feedback_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_versions",
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
        # Model metadata
        sa.Column("model_kind", sa.String(length=32), nullable=False),
        sa.Column("model_json", sa.JSON(), nullable=False),
        # Calibration metrics from the held-out gold set evaluation.
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("training_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Promotion-gate state
        sa.Column("status", sa.String(length=16), nullable=False, server_default="candidate"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        # Audit
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index(
        "ix_policy_versions_active",
        "policy_versions",
        ["organization_id", "role_id", "status"],
    )

    op.create_table(
        "agent_exemplars",
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
        sa.Column("agent_name", sa.String(length=32), nullable=False, index=True),
        # The source feedback row that produced this exemplar.
        sa.Column(
            "source_feedback_id",
            sa.BigInteger(),
            sa.ForeignKey("decision_feedback.id"),
            nullable=True,
        ),
        # Snapshot of the candidate features the agent saw at score time.
        # JSON keyed by canonical signal names ("role_fit_score",
        # "skills_match_pct", "has_python", ...). The retriever computes
        # cosine similarity over the value vector.
        sa.Column("features_json", sa.JSON(), nullable=False),
        # The agent's original score and the recruiter's corrected score.
        sa.Column("agent_score", sa.Float(), nullable=False),
        sa.Column("corrected_score", sa.Float(), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("attributed_reason", sa.Text(), nullable=True),
        # Eviction inputs (D4 formula uses these).
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_agent_exemplars_retrieval",
        "agent_exemplars",
        ["organization_id", "agent_name", "role_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_exemplars_retrieval", table_name="agent_exemplars")
    op.drop_table("agent_exemplars")
    op.drop_index("ix_policy_versions_active", table_name="policy_versions")
    op.drop_table("policy_versions")
