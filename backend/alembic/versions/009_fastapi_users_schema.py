"""Migrate users table for FastAPI-Users

- Rename is_email_verified to is_verified
- Drop password_reset_token, password_reset_expires
- Drop email_verification_token, email_verification_sent_at

Revision ID: 009_fastapi_users
Revises: 008_add_cv_job_match_fields
Create Date: 2026-02-12

"""

from alembic import op
import sqlalchemy as sa


revision = "009_fastapi_users"
down_revision = "008_add_cv_job_match_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename is_email_verified to is_verified (FastAPI-Users convention)
    op.alter_column(
        "users",
        "is_email_verified",
        new_column_name="is_verified",
    )

    # Drop password reset columns (FastAPI-Users uses JWT tokens)
    op.drop_index("ix_users_password_reset_token", table_name="users", if_exists=True)
    op.drop_column("users", "password_reset_token")
    op.drop_column("users", "password_reset_expires")

    # Drop email verification token columns (FastAPI-Users uses JWT tokens)
    op.drop_index("ix_users_email_verification_token", table_name="users", if_exists=True)
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "email_verification_sent_at")


def downgrade() -> None:
    # Restore email verification columns
    op.add_column("users", sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("email_verification_token", sa.String(), nullable=True))
    op.create_index("ix_users_email_verification_token", "users", ["email_verification_token"])

    # Restore password reset columns
    op.add_column("users", sa.Column("password_reset_expires", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_reset_token", sa.String(), nullable=True))
    op.create_index("ix_users_password_reset_token", "users", ["password_reset_token"])

    # Rename is_verified back to is_email_verified
    op.alter_column(
        "users",
        "is_verified",
        new_column_name="is_email_verified",
    )
