"""Add organizations.default_additional_requirements.

Org-wide default for the per-role ``additional_requirements`` field. When a
new role is created (manual API or Workable import) and the role's own
``additional_requirements`` is empty, the org default is copied in.
Recruiters can still edit per-role afterwards — copy-on-create, not
fallback-at-read.

Revision ID: 044_add_default_additional_requirements
Revises: 043_add_cv_match_overrides
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "044_add_default_additional_requirements"
down_revision = "043_add_cv_match_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("default_additional_requirements", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "default_additional_requirements")
