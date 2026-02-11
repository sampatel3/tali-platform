"""add mvp scoring/session fields

Revision ID: 005_add_mvp_scoring_fields
Revises: 004_add_cv_fields_to_assessments
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


revision = "005_add_mvp_scoring_fields"
down_revision = "004_add_cv_fields_to_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("final_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("score_breakdown", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("score_weights_used", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("flags", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessments", sa.Column("total_duration_seconds", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("total_prompts", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("total_input_tokens", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("total_output_tokens", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("tests_run_count", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("tests_pass_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "tests_pass_count")
    op.drop_column("assessments", "tests_run_count")
    op.drop_column("assessments", "total_output_tokens")
    op.drop_column("assessments", "total_input_tokens")
    op.drop_column("assessments", "total_prompts")
    op.drop_column("assessments", "total_duration_seconds")
    op.drop_column("assessments", "scored_at")
    op.drop_column("assessments", "flags")
    op.drop_column("assessments", "score_weights_used")
    op.drop_column("assessments", "score_breakdown")
    op.drop_column("assessments", "final_score")
