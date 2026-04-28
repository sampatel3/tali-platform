"""Add cv_embeddings table for the Phase 2 v4 pre-filter.

Stores embedding vectors keyed on sha256(text, provider, model). One row
per CV (or JD) per provider/model. Used by ``app.cv_matching.embeddings``
to cache vectors and by the runner pre-filter to drop obvious mismatches
before spending Haiku tokens.

Revision ID: 047_add_cv_embeddings
Revises: 046_add_cv_sections
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "047_add_cv_embeddings"
down_revision = "046_add_cv_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_embeddings",
        sa.Column("content_hash", sa.String, primary_key=True),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("embedding", sa.JSON, nullable=False),
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
        "ix_cv_embeddings_provider_model",
        "cv_embeddings",
        ["provider", "model"],
    )


def downgrade() -> None:
    op.drop_index("ix_cv_embeddings_provider_model", table_name="cv_embeddings")
    op.drop_table("cv_embeddings")
