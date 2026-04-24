"""Add role scoring criteria and reject threshold.

Revision ID: 035_add_role_scoring_criteria_threshold
Revises: 034_add_application_score_cache_columns
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "035_add_role_scoring_criteria_threshold"
down_revision = "034_add_application_score_cache_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("scoring_criteria", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "reject_threshold",
                sa.Integer(),
                nullable=False,
                server_default="60",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.drop_column("reject_threshold")
        batch.drop_column("scoring_criteria")
