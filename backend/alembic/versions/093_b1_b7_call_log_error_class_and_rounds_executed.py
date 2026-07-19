"""B1 + B7-step1: error categorization + retry visibility on claude_call_log,
and rounds_executed on agent_runs.

B1 — claude_call_log gains:
- ``error_class``: machine-readable category for failures (rate_limit |
  overloaded | context_length | bad_request | server_error | timeout |
  network | validation | other). Lets dashboards distinguish "Anthropic
  is rate-limiting us" from "we sent garbage" without scraping
  ``error_reason``.
- ``http_status``: numeric status code from the SDK exception, NULL on
  non-HTTP errors.
- ``retry_attempt``: 0 for first try, 1+ for SDK / wrapper retries.
- ``parent_call_log_id``: self-FK so retried calls chain back to the
  original — read a thread of attempts in one query.
- ``trace_id``: caller-supplied id (cv_match retry context, agent
  cycle id) that threads original + retried rows together when there's
  no parent_call_log_id (e.g. cross-process retry).

B7-step1 — agent_runs gains:
- ``rounds_executed``: how many tool-use rounds the cycle actually
  used out of MAX_TOOL_ROUNDS. Used 1-2 weeks post-deploy to tune the
  constant down (if p95 < cap, lower the cap to trim worst-case spend).

All columns are nullable / safe-defaulted so pre-deploy rows keep
working unchanged.

Revision ID: 093_b1_b7_call_log_error_class
Revises: 092_add_input_fingerprint
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "093_b1_b7_call_log_error_class"
down_revision = "092_add_input_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # B1 — claude_call_log
    op.add_column("claude_call_log", sa.Column("error_class", sa.String(), nullable=True))
    op.add_column("claude_call_log", sa.Column("http_status", sa.Integer(), nullable=True))
    op.add_column(
        "claude_call_log",
        sa.Column("retry_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("claude_call_log") as batch_op:
            batch_op.add_column(
                sa.Column("parent_call_log_id", sa.BigInteger(), nullable=True)
            )
            batch_op.create_foreign_key(
                "claude_call_log_parent_call_log_id_fkey",
                "claude_call_log",
                ["parent_call_log_id"],
                ["id"],
            )
    else:
        op.add_column(
            "claude_call_log",
            sa.Column(
                "parent_call_log_id",
                sa.BigInteger(),
                sa.ForeignKey("claude_call_log.id"),
                nullable=True,
            ),
        )
    op.add_column("claude_call_log", sa.Column("trace_id", sa.String(), nullable=True))
    op.create_index(
        "ix_claude_call_log_error_class_created",
        "claude_call_log",
        ["error_class", "created_at"],
    )
    op.create_index("ix_claude_call_log_trace_id", "claude_call_log", ["trace_id"])

    # B7-step1 — agent_runs
    op.add_column("agent_runs", sa.Column("rounds_executed", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "rounds_executed")
    op.drop_index("ix_claude_call_log_trace_id", table_name="claude_call_log")
    op.drop_index("ix_claude_call_log_error_class_created", table_name="claude_call_log")
    op.drop_column("claude_call_log", "trace_id")
    op.drop_column("claude_call_log", "parent_call_log_id")
    op.drop_column("claude_call_log", "retry_attempt")
    op.drop_column("claude_call_log", "http_status")
    op.drop_column("claude_call_log", "error_class")
