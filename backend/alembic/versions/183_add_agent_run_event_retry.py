"""Add durable retry state for terminal agent-run notifications.

Revision ID: 183_agent_run_event_retry
Revises: 182_candidate_clipboard
Create Date: 2026-07-20
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op


revision = "183_agent_run_event_retry"
down_revision = "182_candidate_clipboard"
branch_labels = None
depends_on = None

_DUE_INDEX = "ix_agent_runs_terminal_event_retry_due"
_HISTORICAL_BACKFILL_DAYS = 30


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "terminal_event_reconciled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "terminal_event_failure_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "terminal_event_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "terminal_event_last_error_type",
            sa.String(length=120),
            nullable=True,
        ),
    )
    # Preserve the reconciler's existing 30-day historical boundary once, at
    # migration time. Runtime delivery can then retry every newer source row
    # indefinitely without an event silently aging out during a long outage.
    runs = sa.table(
        "agent_runs",
        sa.column("status", sa.String),
        sa.column("finished_at", sa.DateTime(timezone=True)),
        sa.column("terminal_event_reconciled_at", sa.DateTime(timezone=True)),
    )
    historical_cutoff = datetime.now(timezone.utc) - timedelta(
        days=_HISTORICAL_BACKFILL_DAYS
    )
    op.get_bind().execute(
        runs.update()
        .where(
            runs.c.status.in_(("failed", "aborted", "budget_paused")),
            runs.c.finished_at.isnot(None),
            runs.c.finished_at < historical_cutoff,
            runs.c.terminal_event_reconciled_at.is_(None),
        )
        .values(terminal_event_reconciled_at=runs.c.finished_at)
    )
    op.create_index(
        _DUE_INDEX,
        "agent_runs",
        [
            sa.text("COALESCE(terminal_event_next_attempt_at, finished_at)"),
            "id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "terminal_event_reconciled_at IS NULL "
            "AND status IN ('failed', 'aborted', 'budget_paused') "
            "AND finished_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(_DUE_INDEX, table_name="agent_runs")
    op.drop_column("agent_runs", "terminal_event_last_error_type")
    op.drop_column("agent_runs", "terminal_event_next_attempt_at")
    op.drop_column("agent_runs", "terminal_event_failure_count")
    op.drop_column("agent_runs", "terminal_event_reconciled_at")
