"""Add cv_parse_cache table.

Content-hash cache for parsed CVs. Key is sha256 of (cv_text, prompt_version,
model_version). Identical inputs produce identical results so we can avoid
re-parsing on every fetch.

Revision ID: 045_add_cv_parse_cache
Revises: 044_add_default_additional_requirements
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "045_add_cv_parse_cache"
down_revision = "044_add_default_additional_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_parse_cache",
        sa.Column("cache_key", sa.String, primary_key=True),
        sa.Column("prompt_version", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "last_hit_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_cv_parse_cache_prompt_version", "cv_parse_cache", ["prompt_version"]
    )


def downgrade() -> None:
    op.drop_index("ix_cv_parse_cache_prompt_version", table_name="cv_parse_cache")
    op.drop_table("cv_parse_cache")
