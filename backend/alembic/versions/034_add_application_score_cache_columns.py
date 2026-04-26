"""Add cached TAALI score fields for application list query performance.

Revision ID: 034_add_application_score_cache_columns
Revises: 033_add_pipeline_query_indexes
Create Date: 2026-03-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "034_add_application_score_cache_columns"
down_revision = "033_add_pipeline_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("taali_score_cache_100", sa.Float(), nullable=True))
        batch.add_column(sa.Column("assessment_score_cache_100", sa.Float(), nullable=True))
        batch.add_column(sa.Column("role_fit_score_cache_100", sa.Float(), nullable=True))
        batch.add_column(sa.Column("score_mode_cache", sa.String(), nullable=True))
        batch.add_column(sa.Column("score_cached_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "ix_candidate_applications_org_outcome_taali_sort",
        "candidate_applications",
        [
            "organization_id",
            "deleted_at",
            "application_outcome",
            "taali_score_cache_100",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_candidate_applications_org_role_outcome_taali_sort",
        "candidate_applications",
        [
            "organization_id",
            "role_id",
            "deleted_at",
            "application_outcome",
            "taali_score_cache_100",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_applications_org_role_outcome_taali_sort", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_org_outcome_taali_sort", table_name="candidate_applications")

    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("score_cached_at")
        batch.drop_column("score_mode_cache")
        batch.drop_column("role_fit_score_cache_100")
        batch.drop_column("assessment_score_cache_100")
        batch.drop_column("taali_score_cache_100")
