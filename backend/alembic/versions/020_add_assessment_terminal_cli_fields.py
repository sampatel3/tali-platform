"""add assessment terminal cli fields

Revision ID: 020_add_assessment_terminal_cli_fields
Revises: 019_add_role_interview_focus_fields
Create Date: 2026-02-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "020_add_assessment_terminal_cli_fields"
down_revision = "019_add_role_interview_focus_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("ai_mode", sa.String(), nullable=False, server_default="legacy_chat"))
        batch.add_column(sa.Column("cli_session_pid", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cli_session_state", sa.String(), nullable=True))
        batch.add_column(sa.Column("cli_session_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cli_session_last_seen_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cli_transcript", sa.JSON(), nullable=True))

    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("claude_api_key_encrypted", sa.String(), nullable=True))
        batch.add_column(sa.Column("claude_api_key_last_rotated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("claude_api_key_last_rotated_at")
        batch.drop_column("claude_api_key_encrypted")

    with op.batch_alter_table("assessments") as batch:
        batch.drop_column("cli_transcript")
        batch.drop_column("cli_session_last_seen_at")
        batch.drop_column("cli_session_started_at")
        batch.drop_column("cli_session_state")
        batch.drop_column("cli_session_pid")
        batch.drop_column("ai_mode")
