"""add prompt scoring fields to assessments and tasks

Revision ID: 003
Revises: 002
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Assessment scoring columns
    op.add_column("assessments", sa.Column("prompt_quality_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("prompt_efficiency_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("independence_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("context_utilization_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("design_thinking_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("debugging_strategy_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("written_communication_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("learning_velocity_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("error_recovery_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("requirement_comprehension_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("calibration_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("prompt_fraud_flags", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("prompt_analytics", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("browser_focus_ratio", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("tab_switch_count", sa.Integer(), server_default="0"))
    op.add_column("assessments", sa.Column("time_to_first_prompt_seconds", sa.Integer(), nullable=True))

    # Task scoring configuration columns
    op.add_column("tasks", sa.Column("calibration_prompt", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("score_weights", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("recruiter_weight_preset", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("proctoring_enabled", sa.Boolean(), server_default="false"))


def downgrade() -> None:
    # Task columns
    op.drop_column("tasks", "proctoring_enabled")
    op.drop_column("tasks", "recruiter_weight_preset")
    op.drop_column("tasks", "score_weights")
    op.drop_column("tasks", "calibration_prompt")

    # Assessment columns
    op.drop_column("assessments", "time_to_first_prompt_seconds")
    op.drop_column("assessments", "tab_switch_count")
    op.drop_column("assessments", "browser_focus_ratio")
    op.drop_column("assessments", "prompt_analytics")
    op.drop_column("assessments", "prompt_fraud_flags")
    op.drop_column("assessments", "calibration_score")
    op.drop_column("assessments", "requirement_comprehension_score")
    op.drop_column("assessments", "error_recovery_score")
    op.drop_column("assessments", "learning_velocity_score")
    op.drop_column("assessments", "written_communication_score")
    op.drop_column("assessments", "debugging_strategy_score")
    op.drop_column("assessments", "design_thinking_score")
    op.drop_column("assessments", "context_utilization_score")
    op.drop_column("assessments", "independence_score")
    op.drop_column("assessments", "prompt_efficiency_score")
    op.drop_column("assessments", "prompt_quality_score")
