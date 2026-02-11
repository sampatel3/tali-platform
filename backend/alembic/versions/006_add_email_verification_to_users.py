"""add email verification fields to users

Revision ID: 006_add_email_verification
Revises: 005_add_mvp_scoring_fields
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


revision = "006_add_email_verification"
down_revision = "005_add_mvp_scoring_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("email_verification_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_email_verification_token", "users", ["email_verification_token"])
    # Mark all existing users as verified (they registered before this feature)
    op.execute("UPDATE users SET is_email_verified = true")


def downgrade() -> None:
    op.drop_index("ix_users_email_verification_token", table_name="users")
    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "is_email_verified")
