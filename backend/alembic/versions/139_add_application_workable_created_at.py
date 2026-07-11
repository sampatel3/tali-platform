"""Per-application Workable created_at (= when the candidate applied).

Workable candidate ids are per JOB APPLICATION, but Taali dedups people
into one Candidate row — so ``candidates.workable_created_at`` is
last-sync-wins across a person's applications. This column captures the
applied date for THIS application so freshness can be shown per role.

Adds:
  * ``candidate_applications.workable_created_at`` — DateTime(tz),
    nullable; set by the Workable candidate sync from the application
    payload's ``created_at``.

Revision ID: 139_add_application_workable_created_at
Revises: 138_add_submittal_packs
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "139_add_application_workable_created_at"
down_revision = "138_add_submittal_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("workable_created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "workable_created_at")
