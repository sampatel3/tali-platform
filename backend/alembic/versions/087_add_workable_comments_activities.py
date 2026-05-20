"""Add ``workable_comments`` and ``workable_activities`` JSON columns to
``candidates``.

The pre-screen LLM previously only saw extracted CV text. Hard constraints
expressed only in Workable (e.g. salary expectation in a recruiter comment
or in the candidate's answer to a job questionnaire on LinkedIn apply)
were invisible, so candidates exceeding hard constraints sailed through
pre-screen instead of being filtered out.

Questionnaire answers already arrive in the candidate detail payload and
are persisted in ``workable_data``. Comments and activities sit behind
separate endpoints (``/candidates/{id}/comments`` and
``/candidates/{id}/activities``) and need their own storage so pre-screen
can read them without re-hitting the Workable API.

Revision ID: 087_add_workable_comments_activities
Revises: 086_drop_report_share_columns
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "087_add_workable_comments_activities"
down_revision = "086_drop_report_share_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("workable_comments", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("workable_activities", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.drop_column("workable_activities")
        batch.drop_column("workable_comments")
