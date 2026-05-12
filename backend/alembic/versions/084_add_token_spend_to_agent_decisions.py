"""``agent_decisions.token_spend`` — discipline §8.5: token spend logged per decision.

Adds a single JSONB column populated by the queue_decision wrapper at
queue time. Shape (filled in by ``token_spend_aggregator.aggregate``):

  {
    "input_tokens": 12340,
    "output_tokens": 480,
    "cache_read_tokens": 9800,
    "cache_creation_tokens": 1200,
    "total_micro_usd": 215000,
    "by_agent": {
      "pre_screen":         {"calls": 1, "input": 800,  "output": 60,  "micro_usd": 12000},
      "cv_scoring":         {"calls": 1, "input": 3200, "output": 180, "micro_usd": 95000},
      "graph_priors":       {"calls": 1, "input": 1100, "output": 90,  "micro_usd": 18000},
      "assessment_scoring": {"calls": 1, "input": 7200, "output": 150, "micro_usd": 90000}
    }
  }

The per-call data already lives in the ``usage_events`` table — this
column is a denormalised roll-up keyed by the decision's agent_run_id
so dashboards don't have to join on every query.

Revision ID: 084_add_token_spend
Revises: 083_add_task_calibrations
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "084_add_token_spend"
down_revision = "083_add_task_calibrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_decisions",
        sa.Column(
            "token_spend",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_decisions", "token_spend")
