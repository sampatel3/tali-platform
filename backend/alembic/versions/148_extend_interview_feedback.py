"""extend interview_feedback with the scorecard lifecycle.

Additive-only. Adds the draft/submit lifecycle (``submitted_at``), an optional
per-area score set (``overall_rating`` 1–4 + ``competencies``), and an optional
link to a specific ``application_interviews`` row (``interview_id``). No column
is dropped and no existing value changes semantics.

Backfill semantics: every pre-existing row is treated as SUBMITTED — it was
recorded by a recruiter and already feeds the calibration script. We set
``submitted_at`` = COALESCE(updated_at, created_at) so the calibration consumer,
which now reads only submitted rows, sees exactly what it saw before (zero
behavioral delta at cutover). New rows start as drafts (submitted_at NULL) until
explicitly submitted.

Revision ID: 148_extend_interview_feedback
Revises: 147_add_offer_templates
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "148_extend_interview_feedback"
down_revision = "147_add_offer_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_feedback",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview_feedback",
        sa.Column("overall_rating", sa.Integer(), nullable=True),
    )
    op.add_column(
        "interview_feedback",
        sa.Column("competencies", sa.JSON(), nullable=True),
    )
    op.add_column(
        "interview_feedback",
        sa.Column(
            "interview_id",
            sa.Integer(),
            sa.ForeignKey("application_interviews.id"),
            nullable=True,
        ),
    )

    # Existing rows count as submitted — preserve legacy semantics and keep the
    # calibration consumer's view unchanged.
    op.execute(
        "UPDATE interview_feedback "
        "SET submitted_at = COALESCE(updated_at, created_at) "
        "WHERE submitted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("interview_feedback", "interview_id")
    op.drop_column("interview_feedback", "competencies")
    op.drop_column("interview_feedback", "overall_rating")
    op.drop_column("interview_feedback", "submitted_at")
