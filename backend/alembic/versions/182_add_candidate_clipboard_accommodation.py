"""Add the explicit per-assessment clipboard accommodation.

Revision ID: 182_candidate_clipboard
Revises: 181_bind_candidate_request_proof
"""

from alembic import op
import sqlalchemy as sa


revision = "182_candidate_clipboard"
down_revision = "181_bind_candidate_request_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(
            sa.Column(
                "allow_external_clipboard",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.drop_column("allow_external_clipboard")
