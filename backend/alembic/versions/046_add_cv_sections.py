"""Add cv_sections JSON column to candidate_applications and candidates.

Stores the parsed CV (from the cv_parsing module) so the candidate detail
page can render structured sections without re-running the parser.

Revision ID: 046_add_cv_sections
Revises: 045_add_cv_parse_cache
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "046_add_cv_sections"
down_revision = "045_add_cv_parse_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("cv_sections", sa.JSON, nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column("cv_sections", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidates", "cv_sections")
    op.drop_column("candidate_applications", "cv_sections")
