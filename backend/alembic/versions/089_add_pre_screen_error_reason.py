"""Add ``pre_screen_error_reason`` to ``candidate_applications``.

When the pre-screen LLM call fails (Anthropic credit exhaustion,
network timeout, JSON parse failure, etc.), the orchestrator used to
treat the error as "maybe" and fall through to v3 cv_match scoring.
That produced a high cv_match_score on raw CV-vs-JD fit which then got
mirrored back into ``pre_screen_score_100`` via the refresh helpers —
hiding the actual error from the recruiter and making it look like
pre-screen passed when it never ran.

Going forward: pre-screen errors leave both scores NULL, persist the
error reason in this column so the UI can show "agent couldn't score
this candidate — retry needed" instead of silently passing through.

Revision ID: 089_add_pre_screen_error_reason
Revises: 088_add_role_feedback_notes
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "089_add_pre_screen_error_reason"
down_revision = "088_add_role_feedback_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("pre_screen_error_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("pre_screen_error_reason")
