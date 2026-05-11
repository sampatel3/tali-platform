"""Add validation_status + validation_failures columns to agent_decisions.

Populated by ``validate_agent_decision_evidence`` after each
``queue_decision.run`` call. ``validation_status`` is one of:
``passed`` / ``failed`` / ``skipped`` / NULL (pre-validation rows).
``validation_failures`` is a JSON list of human-readable failure
descriptions when ``validation_status == "failed"``.

Permissive by default: a failed validation does not block the
decision from queueing, but does surface as a warning badge on the
recruiter's pending-decision card so they know to scrutinise the
cited evidence before approving.

Revision ID: 074_add_agent_decision_validation_status
Revises: 073_inject_pre_screen_auto_reject_rule
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "074_add_agent_decision_validation_status"
down_revision = "073_inject_pre_screen_auto_reject_rule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_decisions") as batch:
        batch.add_column(
            sa.Column("validation_status", sa.String(), nullable=True)
        )
        batch.add_column(
            sa.Column("validation_failures", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_decisions") as batch:
        batch.drop_column("validation_failures")
        batch.drop_column("validation_status")
