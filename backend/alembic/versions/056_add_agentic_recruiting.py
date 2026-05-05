"""Agent-native recruiting: per-job agentic mode, decision queue, and run log.

Adds:
- ``roles`` columns: ``agentic_mode_enabled``, allowlist, budget caps,
  pause state, last-run timestamp, and persistent ``agent_calibration``
  blob carried across cycles.
- ``agent_runs``: one row per autonomous cycle (event/cron/manual trigger),
  with token + cost accounting and a snapshot of calibration for replay.
- ``agent_decisions``: queued recommendations the recruiter approves or
  overrides with one click. ``status`` tracks lifecycle; ``idempotency_key``
  stops the agent inserting duplicate decisions on retry.

The new ``technical_interview`` pipeline stage is encoded only in app-level
allowlists (``pipeline_service.PIPELINE_STAGES``); ``candidate_applications.pipeline_stage``
is a free-form string column, so no DDL is needed for the value itself.

Revision ID: 056_add_agentic_recruiting
Revises: 055_add_taali_chat_tables
Create Date: 2026-05-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "056_add_agentic_recruiting"
down_revision = "055_add_taali_chat_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "agentic_mode_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_roles_agentic_mode_enabled",
        "roles",
        ["agentic_mode_enabled"],
    )
    op.add_column("roles", sa.Column("agent_action_allowlist", sa.JSON(), nullable=True))
    op.add_column("roles", sa.Column("agent_token_budget_per_cycle", sa.Integer(), nullable=True))
    op.add_column("roles", sa.Column("agent_decision_budget_per_cycle", sa.Integer(), nullable=True))
    op.add_column("roles", sa.Column("agent_usd_budget_monthly_cents", sa.Integer(), nullable=True))
    op.add_column("roles", sa.Column("agent_paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("roles", sa.Column("agent_paused_reason", sa.Text(), nullable=True))
    op.add_column("roles", sa.Column("agent_last_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("roles", sa.Column("agent_calibration", sa.JSON(), nullable=True))

    op.create_table(
        "agent_runs",
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
            nullable=False,
            index=True,
        ),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column(
            "trigger_event_id",
            sa.Integer(),
            sa.ForeignKey("candidate_application_events.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_micro_usd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("decisions_emitted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tools_called", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("agent_state_snapshot", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_role_started",
        "agent_runs",
        ["role_id", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_org_status",
        "agent_runs",
        ["organization_id", "status"],
    )

    op.create_table(
        "agent_decisions",
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
            nullable=False,
            index=True,
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("candidate_applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "agent_run_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_runs.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("decision_type", sa.String(), nullable=False),
        sa.Column("recommendation", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("override_action", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_decisions_idempotency_key"),
    )
    op.create_index(
        "ix_agent_decisions_role_status",
        "agent_decisions",
        ["role_id", "status"],
    )
    op.create_index(
        "ix_agent_decisions_org_status_created",
        "agent_decisions",
        ["organization_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decisions_org_status_created", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_role_status", table_name="agent_decisions")
    op.drop_table("agent_decisions")

    op.drop_index("ix_agent_runs_org_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_role_started", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_column("roles", "agent_calibration")
    op.drop_column("roles", "agent_last_run_at")
    op.drop_column("roles", "agent_paused_reason")
    op.drop_column("roles", "agent_paused_at")
    op.drop_column("roles", "agent_usd_budget_monthly_cents")
    op.drop_column("roles", "agent_decision_budget_per_cycle")
    op.drop_column("roles", "agent_token_budget_per_cycle")
    op.drop_column("roles", "agent_action_allowlist")
    op.drop_index("ix_roles_agentic_mode_enabled", table_name="roles")
    op.drop_column("roles", "agentic_mode_enabled")
