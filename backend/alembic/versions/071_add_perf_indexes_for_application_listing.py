"""Add indexes that the role-pipeline list query filters/sorts on.

The hot path is ``GET /api/v1/roles/{role_id}/applications`` which always
filters ``(organization_id, role_id, deleted_at IS NULL)`` and frequently
filters by ``status`` or ``pipeline_stage`` and sorts by ``cv_uploaded_at``
or one of the cached score columns. Until now those columns were unindexed,
so the query plan fell back to ``Seq Scan`` on ``candidate_applications``
once the table grew past a few thousand rows.

Schema changes:

- Composite ``(organization_id, role_id, status)`` covers the common
  pipeline filter (active/shortlisted apps for a given role in an org).
- ``cv_uploaded_at`` is sorted on by the recruiter pipeline ("most recent
  CVs first") and used in pre-screen recency checks.
- ``deleted_at`` is filtered on every list query (``deleted_at IS NULL``).

All four are plain b-tree indexes; ``IF NOT EXISTS`` is used so the
migration is idempotent against any environment that already added them
manually.

Revision ID: 071_add_perf_indexes_for_application_listing
Revises: 070_merge_cohort_planner_with_bucketed_decision_merge
Create Date: 2026-05-09
"""

from __future__ import annotations

from alembic import op


revision = "071_add_perf_indexes_for_application_listing"
down_revision = "070_merge_cohort_planner_with_bucketed_decision_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_candidate_applications_org_role_status",
        "candidate_applications",
        ["organization_id", "role_id", "status"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_candidate_applications_cv_uploaded_at",
        "candidate_applications",
        ["cv_uploaded_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_candidate_applications_deleted_at",
        "candidate_applications",
        ["deleted_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_applications_deleted_at",
        table_name="candidate_applications",
        if_exists=True,
    )
    op.drop_index(
        "ix_candidate_applications_cv_uploaded_at",
        table_name="candidate_applications",
        if_exists=True,
    )
    op.drop_index(
        "ix_candidate_applications_org_role_status",
        table_name="candidate_applications",
        if_exists=True,
    )
