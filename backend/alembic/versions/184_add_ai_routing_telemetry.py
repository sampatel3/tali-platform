"""Add provider-neutral AI routing invocation and attempt telemetry.

Revision ID: 184_ai_routing_telemetry
Revises: 183_agent_run_event_retry
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "184_ai_routing_telemetry"
down_revision = "183_agent_run_event_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_routing_invocations",
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("route_id", sa.String(length=36), nullable=False),
        sa.Column("root_invocation_id", sa.String(length=36), nullable=False),
        sa.Column("parent_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("workflow", sa.String(length=120), nullable=False),
        sa.Column("task", sa.String(length=160), nullable=False),
        sa.Column("profile_version", sa.String(length=120), nullable=False),
        sa.Column("policy_version", sa.String(length=120), nullable=False),
        sa.Column("registry_version", sa.String(length=120), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("decision_snapshot", sa.JSON(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("entity_id", sa.String(length=160), nullable=True),
        sa.Column("selected_deployment_id", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="planned",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ai_routing_invocation_status",
        ),
        sa.CheckConstraint(
            "((status IN ('planned', 'running') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_ai_routing_invocation_finished",
        ),
        sa.CheckConstraint(
            "((status = 'planned' AND started_at IS NULL) OR "
            "(status IN ('running', 'succeeded') AND started_at IS NOT NULL) OR "
            "status IN ('failed', 'cancelled'))",
            name="ck_ai_routing_invocation_started",
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
    )
    op.create_index(
        "ix_ai_routing_invocation_route",
        "ai_routing_invocations",
        ["route_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_invocation_root",
        "ai_routing_invocations",
        ["root_invocation_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_invocation_parent",
        "ai_routing_invocations",
        ["parent_invocation_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_invocation_task_created",
        "ai_routing_invocations",
        ["workflow", "task", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_invocation_org_created",
        "ai_routing_invocations",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_invocation_status_created",
        "ai_routing_invocations",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "ai_routing_attempts",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("iteration_ordinal", sa.Integer(), nullable=False),
        sa.Column("attempt_in_iteration", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("runtime", sa.String(length=80), nullable=False),
        sa.Column("deployment_id", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column(
            "region", sa.String(length=32), server_default="global", nullable=False
        ),
        sa.Column("pricing_id", sa.String(length=160), nullable=True),
        sa.Column("credit_reservation_ref", sa.String(length=255), nullable=False),
        sa.Column("estimated_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("estimated_output_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "estimated_input_cost_basis",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("admitted_cost_usd_micro", sa.BigInteger(), nullable=False),
        sa.Column(
            "fallback_from_deployment_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("fallback_reason", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error_class", sa.String(length=120), nullable=True),
        sa.Column("error_reason", sa.String(length=160), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_creation_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd_micro", sa.BigInteger(), nullable=True),
        sa.Column(
            "usage_unknown",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("usage_event_id", sa.Integer(), nullable=True),
        sa.Column("claude_call_log_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ordinal >= 1", name="ck_ai_routing_attempt_ordinal"),
        sa.CheckConstraint(
            "iteration_ordinal >= 1",
            name="ck_ai_routing_attempt_iteration_ordinal",
        ),
        sa.CheckConstraint(
            "attempt_in_iteration >= 1",
            name="ck_ai_routing_attempt_in_iteration",
        ),
        sa.CheckConstraint(
            "estimated_input_tokens >= 0",
            name="ck_ai_routing_attempt_estimated_input_tokens",
        ),
        sa.CheckConstraint(
            "estimated_output_tokens > 0",
            name="ck_ai_routing_attempt_estimated_output_tokens",
        ),
        sa.CheckConstraint(
            "estimated_input_cost_basis IN "
            "('standard', 'cache_write_5m', 'cache_write_1h')",
            name="ck_ai_routing_attempt_estimated_input_cost_basis",
        ),
        sa.CheckConstraint(
            "admitted_cost_usd_micro >= 0",
            name="ck_ai_routing_attempt_admitted_cost",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'ambiguous', 'cancelled')",
            name="ck_ai_routing_attempt_status",
        ),
        sa.CheckConstraint(
            "((status IN ('pending', 'running') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_ai_routing_attempt_finished",
        ),
        sa.CheckConstraint(
            "((status = 'pending' AND started_at IS NULL) OR "
            "(status != 'pending' AND started_at IS NOT NULL))",
            name="ck_ai_routing_attempt_started",
        ),
        sa.CheckConstraint(
            "((fallback_from_deployment_id IS NULL AND fallback_reason IS NULL) OR "
            "(fallback_from_deployment_id IS NOT NULL AND fallback_reason IS NOT NULL))",
            name="ck_ai_routing_attempt_fallback_complete",
        ),
        sa.CheckConstraint(
            "((status IN ('failed', 'ambiguous') AND error_class IS NOT NULL) OR "
            "(status = 'succeeded' AND error_class IS NULL AND error_reason IS NULL) OR "
            "status IN ('pending', 'running', 'cancelled'))",
            name="ck_ai_routing_attempt_error_complete",
        ),
        sa.CheckConstraint(
            "((status IN ('pending', 'running') AND latency_ms IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND latency_ms IS NOT NULL AND latency_ms >= 0))",
            name="ck_ai_routing_attempt_latency",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_ai_routing_attempt_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_ai_routing_attempt_output_tokens",
        ),
        sa.CheckConstraint(
            "cache_read_tokens IS NULL OR cache_read_tokens >= 0",
            name="ck_ai_routing_attempt_cache_read_tokens",
        ),
        sa.CheckConstraint(
            "cache_creation_tokens IS NULL OR cache_creation_tokens >= 0",
            name="ck_ai_routing_attempt_cache_creation_tokens",
        ),
        sa.CheckConstraint(
            "cost_usd_micro IS NULL OR cost_usd_micro >= 0",
            name="ck_ai_routing_attempt_cost",
        ),
        sa.CheckConstraint(
            "((status IN ('pending', 'running') AND (NOT usage_unknown) "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND cache_read_tokens IS NULL AND cache_creation_tokens IS NULL "
            "AND cost_usd_micro IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND usage_unknown AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND cache_read_tokens IS NULL AND cache_creation_tokens IS NULL "
            "AND cost_usd_micro IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND (NOT usage_unknown) AND input_tokens IS NOT NULL "
            "AND output_tokens IS NOT NULL AND cache_read_tokens IS NOT NULL "
            "AND cache_creation_tokens IS NOT NULL AND cost_usd_micro IS NOT NULL))",
            name="ck_ai_routing_attempt_usage_complete",
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["ai_routing_invocations.invocation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invocation_id",
            "ordinal",
            name="uq_ai_routing_attempt_invocation_ordinal",
        ),
        sa.UniqueConstraint(
            "credit_reservation_ref",
            name="uq_ai_routing_attempt_credit_reservation",
        ),
    )
    op.create_index(
        "ix_ai_routing_attempt_deployment_created",
        "ai_routing_attempts",
        ["deployment_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_attempt_status_created",
        "ai_routing_attempts",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_attempt_usage_event",
        "ai_routing_attempts",
        ["usage_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_routing_attempt_claude_call",
        "ai_routing_attempts",
        ["claude_call_log_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_routing_attempt_claude_call", table_name="ai_routing_attempts")
    op.drop_index("ix_ai_routing_attempt_usage_event", table_name="ai_routing_attempts")
    op.drop_index(
        "ix_ai_routing_attempt_status_created", table_name="ai_routing_attempts"
    )
    op.drop_index(
        "ix_ai_routing_attempt_deployment_created", table_name="ai_routing_attempts"
    )
    op.drop_table("ai_routing_attempts")
    op.drop_index(
        "ix_ai_routing_invocation_status_created",
        table_name="ai_routing_invocations",
    )
    op.drop_index(
        "ix_ai_routing_invocation_org_created", table_name="ai_routing_invocations"
    )
    op.drop_index(
        "ix_ai_routing_invocation_task_created", table_name="ai_routing_invocations"
    )
    op.drop_index(
        "ix_ai_routing_invocation_parent", table_name="ai_routing_invocations"
    )
    op.drop_index("ix_ai_routing_invocation_root", table_name="ai_routing_invocations")
    op.drop_index("ix_ai_routing_invocation_route", table_name="ai_routing_invocations")
    op.drop_table("ai_routing_invocations")
