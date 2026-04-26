"""Add cv_match_overrides table.

Captures recruiter overrides of cv_match_v3.0 recommendations. Each row
records the original (LLM-derived) outcome, the recruiter-supplied override,
and free-text notes for later analysis. The table is append-only; we don't
update prior overrides on re-score.

Linked to ``users.id`` for the recruiter and to ``candidate_applications.id``
for the candidate. The original trace_id is stored as a string (no FK — the
trace log lives outside the DB) so an admin can correlate to telemetry.

Revision ID: 043_add_cv_match_overrides
Revises: 042_drop_recruiter_workflow_v2_enabled
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "043_add_cv_match_overrides"
down_revision = "042_drop_recruiter_workflow_v2_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_match_overrides",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "application_id",
            sa.Integer,
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recruiter_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("original_trace_id", sa.String, nullable=True),
        sa.Column("original_recommendation", sa.String, nullable=True),
        sa.Column("override_recommendation", sa.String, nullable=False),
        sa.Column("original_score", sa.Float, nullable=True),
        sa.Column("recruiter_notes", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_cv_match_overrides_application_id",
        "cv_match_overrides",
        ["application_id"],
    )
    op.create_index(
        "ix_cv_match_overrides_recruiter_id",
        "cv_match_overrides",
        ["recruiter_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cv_match_overrides_recruiter_id", table_name="cv_match_overrides")
    op.drop_index("ix_cv_match_overrides_application_id", table_name="cv_match_overrides")
    op.drop_table("cv_match_overrides")
