"""Remove the Taali offers feature — drop offer tables.

The ATS owns offers; Taali is a complementary layer, not a replacement. The
offer lifecycle / approvals / templates were flag-off (dark) in prod and are
being removed. Tables are empty (no seed), so there is no data migration.

Drops ``offer_approvals`` (FK -> offers) before ``offers``, then
``offer_templates``. ``downgrade`` recreates all three, mirroring the original
upgrades in 145_add_offers and 147_add_offer_templates, so the graph stays
reversible.

Revision ID: 157_drop_offers
Revises: 156_add_data_subject_requests
Create Date: 2026-07-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "157_drop_offers"
down_revision = "156_add_data_subject_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FK order: approvals reference offers, so drop approvals first.
    op.drop_index("ix_offer_approvals_offer", table_name="offer_approvals")
    op.drop_index("ix_offer_approvals_id", table_name="offer_approvals")
    op.drop_table("offer_approvals")

    op.drop_index("ix_offers_org_application", table_name="offers")
    op.drop_index("ix_offers_id", table_name="offers")
    op.drop_table("offers")

    op.drop_index("ix_offer_templates_org", table_name="offer_templates")
    op.drop_table("offer_templates")


def downgrade() -> None:
    # Recreate mirrors 145_add_offers + 147_add_offer_templates.
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("candidate_applications.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("base_salary_amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("pay_frequency", sa.String(), nullable=True),
        sa.Column("signing_bonus", sa.Integer(), nullable=True),
        sa.Column("equity_units", sa.Integer(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "application_id", "version", name="uq_offers_application_version"
        ),
    )
    op.create_index("ix_offers_id", "offers", ["id"])
    op.create_index(
        "ix_offers_org_application", "offers", ["organization_id", "application_id"]
    )

    op.create_table(
        "offer_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("offers.id"), nullable=False),
        sa.Column("group_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("group_quorum", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approver_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_offer_approvals_id", "offer_approvals", ["id"])
    op.create_index("ix_offer_approvals_offer", "offer_approvals", ["offer_id"])

    op.create_table(
        "offer_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_salary_amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("pay_frequency", sa.String(), nullable=True),
        sa.Column("signing_bonus", sa.Integer(), nullable=True),
        sa.Column("equity_units", sa.Integer(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_offer_templates_org", "offer_templates", ["organization_id"]
    )
