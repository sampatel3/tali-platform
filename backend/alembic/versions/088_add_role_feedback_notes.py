"""Add ``role_feedback_notes`` — recruiter-authored freeform feedback on a role.

Recruiters notice trends ("the agent keeps over-weighting recent SaaS
experience", "we want to see more candidates with NGO backgrounds")
that don't belong on a single decision. ``decision_feedback`` is per
decision; ``role_intents`` is the manually-curated structured overlay.
This table is the middle path: a timestamped append-only log of
freeform notes the recruiter writes for the agent, scoped to a role.

Each row is shown in the role's "Feedback to the agent" timeline so
the recruiter can audit what they've told the agent over time, and the
most-recent N rows are inlined into the agent's system prompt on every
cycle so the agent reads them alongside the active ``role_intent``.

Revision ID: 088_add_role_feedback_notes
Revises: 087_add_workable_comments_activities
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "088_add_role_feedback_notes"
down_revision = "087_add_workable_comments_activities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_feedback_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_role_feedback_notes_role_created",
        "role_feedback_notes",
        ["role_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_feedback_notes_role_created", table_name="role_feedback_notes")
    op.drop_table("role_feedback_notes")
