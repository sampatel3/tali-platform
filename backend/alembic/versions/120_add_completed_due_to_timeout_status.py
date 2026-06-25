"""Add COMPLETED_DUE_TO_TIMEOUT to the assessmentstatus enum.

The timeout-finalize paths set ``status = COMPLETED_DUE_TO_TIMEOUT`` — both the
server-side sweep (``finalize_timed_out_assessments``, PR #698) and the
pre-existing pull-based ``_auto_submit_on_timeout``. But the value was never
added to the prod Postgres enum: it only carried PENDING, IN_PROGRESS, COMPLETED,
EXPIRED. Writing the missing value therefore errored on commit, so a candidate
who ran out of time could not be finalized. This adds it.

SQLite (used in tests via create_all) stores the enum as a permissive VARCHAR, so
this is a Postgres-only operation — a no-op elsewhere.

Revision ID: 120_add_completed_due_to_timeout_status
Revises: 119_add_application_manual_decision
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op

revision = "120_add_completed_due_to_timeout_status"
down_revision = "119_add_application_manual_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PG 12+ permits ADD VALUE inside a transaction provided the new value is
        # not USED in the same transaction (it isn't here). IF NOT EXISTS makes
        # this idempotent / safe to re-run.
        op.execute(
            "ALTER TYPE assessmentstatus ADD VALUE IF NOT EXISTS 'COMPLETED_DUE_TO_TIMEOUT'"
        )


def downgrade() -> None:
    # Postgres cannot drop an enum value without recreating the type; the value
    # is purely additive and harmless, so downgrade is intentionally a no-op.
    pass
