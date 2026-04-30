"""Drop legacy assessment quota columns.

The Lemon-Squeezy fixed-price model tracked per-org consumption via
``organizations.assessments_used`` (incrementing) and an optional
``organizations.assessments_limit`` cap. Usage-based pricing replaces both
with ``usage_events`` rollups + ``credits_balance`` micro-credit math, so
the columns are dead weight.

Revision ID: 053_drop_legacy_assessment_quota
Revises: 052_add_usage_based_pricing
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "053_drop_legacy_assessment_quota"
down_revision = "052_add_usage_based_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("organizations", "assessments_used")
    op.drop_column("organizations", "assessments_limit")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "assessments_used",
            sa.Integer(),
            nullable=True,
            server_default="0",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("assessments_limit", sa.Integer(), nullable=True),
    )
