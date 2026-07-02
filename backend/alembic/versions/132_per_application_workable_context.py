"""Per-application Workable context.

Workable candidate ids — and their answers/comments/activities payloads —
are per JOB APPLICATION, but Taali dedups people into one ``Candidate``
row. A person with applications on several roles therefore had each
role's sync overwrite the shared candidate-level context (last sync
wins), so role A's scoring context could show role B's questionnaire
answers. Store the context on the application itself; candidate-level
fields remain as a legacy fallback for rows synced before this.

Adds (all JSON, nullable — backfilled lazily by the next full sync):
  * ``candidate_applications.workable_answers``
  * ``candidate_applications.workable_comments``
  * ``candidate_applications.workable_activities``

Revision ID: 132_per_application_workable_context
Revises: 131_add_role_brief_client_messages
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "132_per_application_workable_context"
down_revision = "131_add_role_brief_client_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("workable_answers", sa.JSON(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("workable_comments", sa.JSON(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("workable_activities", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "workable_activities")
    op.drop_column("candidate_applications", "workable_comments")
    op.drop_column("candidate_applications", "workable_answers")
