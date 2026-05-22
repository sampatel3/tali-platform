"""C4: cross-cycle decision dedup key.

Adds ``AgentDecision.decision_dedup_key`` — a hash of
``(application_id, decision_type, criteria_fingerprint, cv_fingerprint,
pre_screen_score_bucket, cv_match_score_bucket)``. Lets
``queue_decision.run`` dedupe across agent cycles: the existing
"one pending per app" guard catches the pending case, and the C3
"recently discarded" guard catches re-emits in the 10-min window after
dismissal — but neither catches the case where the prior decision is
approved/expired/discarded older than 10min and the agent re-runs minutes
later with identical inputs.

Real risk for ``send_assessment`` (which sends a candidate-facing email
on approval): same candidate, two cycles, two approved decisions =
two emails.

Non-unique index because intentional re-emit after inputs change (fresh
CV → fresh fingerprint → fresh dedup key) must work. The dedup logic
in queue_decision is responsible for "look for the same key resolved
recently" — the DB just makes it cheap to query.

Revision ID: 094_c4_decision_dedup_key
Revises: 093_b1_b7_call_log_error_class
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "094_c4_decision_dedup_key"
down_revision = "093_b1_b7_call_log_error_class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_decisions",
        sa.Column("decision_dedup_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_decisions_dedup_key",
        "agent_decisions",
        ["decision_dedup_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decisions_dedup_key", table_name="agent_decisions")
    op.drop_column("agent_decisions", "decision_dedup_key")
