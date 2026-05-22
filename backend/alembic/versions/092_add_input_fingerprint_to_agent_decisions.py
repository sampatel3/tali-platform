"""A1: input fingerprint on agent_decisions for staleness detection.

Adds three columns so the read-time staleness service (A2) can detect
when the inputs cited by a queued decision have shifted between queue
time and approval time — and so the audit row for resolved candidates
keeps an immutable record of what the inputs looked like when the agent
decided.

Columns:

- ``input_fingerprint`` (JSON): forensic snapshot of every input the
  agent cited. Shape:
    {criteria_fingerprint: str (sha256 of sorted active role_criteria),
     cv_fingerprint: str (sha256 of cv_text at emit),
     cv_uploaded_at: iso8601,
     pre_screen_score_at_emit: float|None,
     assessment_score_at_emit: float|None,
     taali_score_at_emit: float|None,
     pre_screen_cutoff_at_emit: float|None,
     role_intent_revision_id: int|None,
     last_recruiter_note_id: int|None}
- ``criteria_fingerprint`` (String(64), indexed): scalar shortcut so
  the drift detector can WHERE on it without JSON-extract.
- ``cv_fingerprint`` (String(64)): scalar shortcut for the CV hash.

All three are nullable / safe-defaulted so pre-A1 rows aren't broken.

Revision ID: 092_add_input_fingerprint
Revises: 091_add_role_tech_questions_cache
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "092_add_input_fingerprint"
down_revision = "091_add_role_tech_questions_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_decisions",
        sa.Column(
            "input_fingerprint",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "agent_decisions",
        sa.Column("criteria_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_decisions",
        sa.Column("cv_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_decisions_criteria_fingerprint",
        "agent_decisions",
        ["criteria_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decisions_criteria_fingerprint", table_name="agent_decisions")
    op.drop_column("agent_decisions", "cv_fingerprint")
    op.drop_column("agent_decisions", "criteria_fingerprint")
    op.drop_column("agent_decisions", "input_fingerprint")
