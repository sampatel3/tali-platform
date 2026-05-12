"""``decision_feedback`` — attributed-feedback columns for the multi-agent upgrade.

Adds three columns described in the recruiter-agent architecture spec §6.5:

  attributed_to     which sub-agent (or policy_combination) was wrong
  direction         over / under — did the agent score too high or too low
  graph_write_hints structured suggestions to mutate the graph (JSON list)

Recruiter UI v2 captures these on every teach event; the policy fitter +
graph writeback pipeline read them. All three are nullable so existing
rows stay valid — pre-upgrade teach events become "unattributed" and
flow through the policy fitter with the default weight.

Revision ID: 077_add_attributed_feedback_columns
Revises: 076_drop_legacy_role_auto_reject_columns
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "077_add_attributed_feedback_columns"
down_revision = "076_drop_legacy_role_auto_reject_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_feedback",
        sa.Column("attributed_to", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("direction", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("graph_write_hints", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decision_feedback", "graph_write_hints")
    op.drop_column("decision_feedback", "direction")
    op.drop_column("decision_feedback", "attributed_to")
