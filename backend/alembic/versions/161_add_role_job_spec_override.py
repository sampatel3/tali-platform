"""Track recruiter-authored job-spec overrides.

Revision ID: 161_add_role_job_spec_override
Revises: 160_add_candidate_search_indexes
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "161_add_role_job_spec_override"
down_revision = "160_add_candidate_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("job_spec_manually_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill provenance that predates this explicit marker. Historic Taali
    # upload and agent-edit paths changed job_spec_text + uploaded_at but left
    # the ATS-owned ``description`` untouched; non-ATS roles with a spec are
    # Taali-authored by definition. A user-uploaded original filename is a
    # second durable signal (ATS snapshots use job-spec-<role>.txt).
    #
    # Ambiguous ATS rows are intentionally left NULL here. Their next sync
    # compares the current text with the *previous* cached raw ATS payload via
    # job_spec_override_service before accepting a remote replacement.
    op.execute(
        """
        UPDATE roles
        SET
            job_spec_manually_edited_at = COALESCE(
                job_spec_uploaded_at,
                updated_at,
                created_at,
                now()
            ),
            description = job_spec_text
        WHERE job_spec_manually_edited_at IS NULL
          AND NULLIF(BTRIM(job_spec_text), '') IS NOT NULL
          AND (
              COALESCE(LOWER(source), 'manual') NOT IN ('workable', 'bullhorn')
              OR job_spec_text IS DISTINCT FROM description
              OR (
                  NULLIF(BTRIM(job_spec_filename), '') IS NOT NULL
                  AND NOT (
                      LOWER(job_spec_filename) LIKE 'job-spec-%.txt'
                  )
              )
          )
        """
    )


def downgrade() -> None:
    op.drop_column("roles", "job_spec_manually_edited_at")
