"""Add candidate_applications scoring-error backoff columns.

Auto-scoring (``_auto_enqueue_scoring`` → ``enqueue_score``) re-enqueued a
job for every open, has-cv-text, unscored candidate every 30-min cohort
tick. A 6h backoff already covered *pre-screen* errors, but the full v3
``cv_match`` failure path (``run_cv_match`` returns ``scoring_status=failed``
on an Anthropic credit-balance 400 — it never raises) marked the job
``error`` without stamping any backoff marker. Candidates that passed
pre-screen but whose full score kept failing were therefore retried every
tick: roles 110-113 accumulated 50-69 failed cv_score_jobs each during the
2026-05-24 credit outage.

These two columns drive a unified exponential backoff keyed on the
application:
  - ``score_error_count``: consecutive failed scoring attempts (pre-screen
    error OR v3 failure) since the last success / CV change.
  - ``score_retry_after``: earliest time the next auto attempt may run,
    set to ``now + min(base * 2^(count-1), cap)`` on each error.

Both default to "no backoff" (count 0, retry_after NULL). No backfill: any
candidate still failing gets exactly one more attempt on the next tick,
which seeds count=1 and engages the backoff from then on.

Revision ID: 103_add_scoring_error_backoff
Revises: 102_add_genuine_pre_screen_score
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "103_add_scoring_error_backoff"
down_revision = "102_add_genuine_pre_screen_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column(
            "score_error_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("score_retry_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "score_retry_after")
    op.drop_column("candidate_applications", "score_error_count")
