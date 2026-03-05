"""Add composite indexes for recruiter workflow v2 pipeline queries.

Revision ID: 033_add_pipeline_query_indexes
Revises: 032_jobs_first_pipeline_v2
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op


revision = "033_add_pipeline_query_indexes"
down_revision = "032_jobs_first_pipeline_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_candidate_applications_org_role_outcome_stage",
        "candidate_applications",
        [
            "organization_id",
            "role_id",
            "deleted_at",
            "application_outcome",
            "pipeline_stage",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_candidate_applications_org_outcome_stage",
        "candidate_applications",
        [
            "organization_id",
            "deleted_at",
            "application_outcome",
            "pipeline_stage",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_candidate_application_events_org_app_created",
        "candidate_application_events",
        ["organization_id", "application_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_application_events_org_app_created", table_name="candidate_application_events")
    op.drop_index("ix_candidate_applications_org_outcome_stage", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_org_role_outcome_stage", table_name="candidate_applications")

