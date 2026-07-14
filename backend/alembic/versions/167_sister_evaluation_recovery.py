"""Add durable recovery leases to related-role scoring.

Revision ID: 167_sister_eval_recovery
Revises: 166_application_ingest_outbox
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "167_sister_eval_recovery"
down_revision = "166_application_ingest_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sister_role_evaluations",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column("dispatch_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_sister_evaluations_recovery",
        "sister_role_evaluations",
        ["status", "next_attempt_at"],
    )
    # Earlier code used ``error`` for broker outages and transient provider
    # failures. Put those rows on the autonomous recovery rail at rollout.
    op.execute(
        "UPDATE sister_role_evaluations "
        "SET status = 'retry_wait', next_attempt_at = CURRENT_TIMESTAMP, "
        "last_error_code = 'legacy_transient_failure', "
        "error_message = 'legacy_transient_failure' "
        "WHERE status = 'error'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE sister_role_evaluations SET status = 'error' "
        "WHERE status = 'retry_wait'"
    )
    op.drop_index(
        "ix_sister_evaluations_recovery", table_name="sister_role_evaluations"
    )
    op.drop_column("sister_role_evaluations", "last_error_code")
    op.drop_column("sister_role_evaluations", "dispatch_attempted_at")
    op.drop_column("sister_role_evaluations", "next_attempt_at")
    op.drop_column("sister_role_evaluations", "attempts")
