"""Drop the write-dead ``assessments.cli_session_*`` columns.

The four ``cli_session_pid/_state/_started_at/_last_seen_at`` columns tracked a
live CLI-terminal session for the legacy terminal assessment flow, which was
removed in #725. They have had no writers since, so they are permanently NULL.

NOTE: ``cli_transcript`` (added by the same migration, 020) is intentionally
KEPT — it has live readers in submission_runtime.py (scoring token totals) and
billing_routes.py (assessment cost).

Drops:
  * ``assessments.cli_session_pid`` — Integer, nullable.
  * ``assessments.cli_session_state`` — String, nullable.
  * ``assessments.cli_session_started_at`` — DateTime(timezone=True), nullable.
  * ``assessments.cli_session_last_seen_at`` — DateTime(timezone=True), nullable.

Revision ID: 130_drop_assessment_cli_session_fields
Revises: 129_drop_role_agent_next_run_at
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "130_drop_assessment_cli_session_fields"
down_revision = "129_drop_role_agent_next_run_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("assessments", "cli_session_last_seen_at")
    op.drop_column("assessments", "cli_session_started_at")
    op.drop_column("assessments", "cli_session_state")
    op.drop_column("assessments", "cli_session_pid")


def downgrade() -> None:
    op.add_column("assessments", sa.Column("cli_session_pid", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("cli_session_state", sa.String(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("cli_session_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("cli_session_last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
