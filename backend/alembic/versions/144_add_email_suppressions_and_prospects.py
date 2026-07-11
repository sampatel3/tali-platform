"""Outreach foundations: email_suppressions + prospects tables.

Revision ID: 142_add_email_suppressions_and_prospects
Revises: 141_add_auth_hardening
"""

from alembic import op
import sqlalchemy as sa

revision = "144_add_email_suppressions_and_prospects"
down_revision = "143_audit_event_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Email suppression list. organization_id NULL = platform-global row
    # (hard bounces / complaints protecting the shared sender domain); a set
    # org_id = that org's unsubscribes / manual blocks.
    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("email_normalized", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "email_normalized",
            name="uq_email_suppression_org_email",
        ),
    )
    op.create_index(
        "ix_email_suppressions_organization_id",
        "email_suppressions",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_suppressions_email_normalized",
        "email_suppressions",
        ["email_normalized"],
    )

    # Sourced prospects — lightweight outreach leads, not yet full candidates.
    op.create_table(
        "prospects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("candidates.id"),
            nullable=True,
        ),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_strategy", sa.String(), nullable=True),
        sa.Column("source_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id", "email", name="uq_prospect_org_email"
        ),
    )
    op.create_index(
        "ix_prospects_organization_id", "prospects", ["organization_id"]
    )
    op.create_index("ix_prospects_candidate_id", "prospects", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_prospects_candidate_id", table_name="prospects")
    op.drop_index("ix_prospects_organization_id", table_name="prospects")
    op.drop_table("prospects")
    op.drop_index(
        "ix_email_suppressions_email_normalized", table_name="email_suppressions"
    )
    op.drop_index(
        "ix_email_suppressions_organization_id", table_name="email_suppressions"
    )
    op.drop_table("email_suppressions")
