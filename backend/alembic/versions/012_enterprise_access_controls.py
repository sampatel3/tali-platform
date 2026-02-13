"""Add enterprise access-control fields to organizations

Revision ID: 012
Revises: 011_add_manual_evaluation
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa

revision = "012_enterprise_access_controls"
down_revision = "011_add_manual_evaluation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("allowed_email_domains", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("sso_enforced", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organizations", sa.Column("saml_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organizations", sa.Column("saml_metadata_url", sa.String(), nullable=True))

    # Drop server defaults after backfill so app-level defaults remain source of truth.
    op.alter_column("organizations", "sso_enforced", server_default=None)
    op.alter_column("organizations", "saml_enabled", server_default=None)


def downgrade():
    op.drop_column("organizations", "saml_metadata_url")
    op.drop_column("organizations", "saml_enabled")
    op.drop_column("organizations", "sso_enforced")
    op.drop_column("organizations", "allowed_email_domains")
