"""ATS slice B: reusable offer templates.

Additive: ``offer_templates`` — org-level default compensation (a band/level
template) that an offer can be created from (prefills the typed comp fields).

``is_active`` is created NOT NULL with no server default on purpose: the model
carries a Python-side ``default=True`` (a string server_default of 'true' broke
``.is_(True)`` on sqlite), so rows are only ever written through the ORM.

Revision ID: 147_add_offer_templates
Revises: 146_add_job_hiring_team
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "147_add_offer_templates"
down_revision = "146_add_job_hiring_team"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_offer_templates_org", table_name="offer_templates")
    op.drop_table("offer_templates")
