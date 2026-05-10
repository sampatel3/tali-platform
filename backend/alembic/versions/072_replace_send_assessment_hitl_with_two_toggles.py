"""Replace agent_send_assessment_requires_approval with two HITL toggles.

The original cohort-planner HITL design exposed a single per-role flag
(``agent_send_assessment_requires_approval``) that gated only the
send-assessment tool. Recruiters asked for two simpler controls that
match how they actually think about agent autonomy:

  * ``auto_reject``   — when True, the agent (and the pre-screen Celery
    auto-reject path) executes reject decisions immediately; when False
    every reject lands in the Decision Hub for human approval.
  * ``auto_promote``  — when True, the agent sends assessments and
    advances candidates to interview without approval; when False both
    actions queue as Decision Hub cards.

Both default ``False`` so turning agent mode on never causes a
candidate-visible action without an explicit click.

The old single-purpose flag is migrated:
  agent_send_assessment_requires_approval=True  → auto_promote=False
  agent_send_assessment_requires_approval=False → auto_promote=True

Revision ID: 072_replace_send_assessment_hitl_with_two_toggles
Revises: 071_add_perf_indexes_for_application_listing
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "072_replace_send_assessment_hitl_with_two_toggles"
down_revision = "071_add_perf_indexes_for_application_listing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "auto_reject",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "auto_promote",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Migrate existing values: requires_approval=True maps to auto=False.
    op.execute(
        "UPDATE roles SET auto_promote = NOT agent_send_assessment_requires_approval"
    )
    op.drop_column("roles", "agent_send_assessment_requires_approval")


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "agent_send_assessment_requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.execute(
        "UPDATE roles SET agent_send_assessment_requires_approval = NOT auto_promote"
    )
    op.drop_column("roles", "auto_promote")
    op.drop_column("roles", "auto_reject")
