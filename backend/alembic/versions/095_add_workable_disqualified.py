"""Add ``workable_disqualified`` + ``workable_disqualified_at`` to ``candidate_applications``.

Workable keeps a candidate in their stage (e.g. "Technical Interview") even
after they're disqualified — disqualification is a separate overlay flag, not
a stage. The sync previously skipped disqualified candidates entirely, so
their row was never updated and Tali kept showing the stale pre-disqualify
stage with no indication the person was out.

These columns let the sync record the disqualified state explicitly so the UI
can surface a "Disqualified" badge and the candidate can be moved to Tali's
terminal ``advanced`` stage.

Revision ID: 095_add_workable_disqualified
Revises: 094_c4_decision_dedup_key
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "095_add_workable_disqualified"
down_revision = "094_c4_decision_dedup_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("workable_disqualified", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("workable_disqualified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("workable_disqualified_at")
        batch.drop_column("workable_disqualified")
