"""Drop the candidate-facing feedback report columns.

The candidate feedback report feature was removed entirely (it was never wired
into any UI and Taali does not email candidates about feedback). Drop the now-
unused ``candidate_feedback_*`` columns from ``assessments`` and the
``candidate_feedback_enabled`` toggle from ``organizations``. The interview
debrief columns added by 029 are KEPT — that feature stays.

Revision ID: 117_drop_candidate_feedback
Revises: 116_add_workable_stage_local_write_at
Create Date: 2026-06-14

"""

from alembic import op
import sqlalchemy as sa


revision = "117_drop_candidate_feedback"
down_revision = "116_add_workable_stage_local_write_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("organizations", "candidate_feedback_enabled")
    op.drop_column("assessments", "candidate_feedback_enabled")
    op.drop_column("assessments", "candidate_feedback_ready")
    op.drop_column("assessments", "candidate_feedback_sent_at")
    op.drop_column("assessments", "candidate_feedback_generated_at")
    op.drop_column("assessments", "candidate_feedback_json")


def downgrade() -> None:
    # Recreate the columns with their original definitions (data is not restored).
    op.add_column("assessments", sa.Column("candidate_feedback_json", sa.JSON(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("candidate_feedback_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("candidate_feedback_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "candidate_feedback_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "candidate_feedback_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "candidate_feedback_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
