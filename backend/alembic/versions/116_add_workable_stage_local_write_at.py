"""Local-write-wins guard for workable_stage.

``candidate_applications.workable_stage_local_write_at`` records WHEN Taali
itself last set ``workable_stage`` (a recruiter advance / move that Taali wrote
to Workable). The candidate sync consults it so it never clobbers a stage Taali
moved seconds ago with a stale bulk-list snapshot still propagating in Workable.
After a short guard window the sync wins again (Workable has settled).

Revision ID: 116_add_workable_stage_local_write_at
Revises: 115_prescreen_auto_threshold_default
Create Date: 2026-06-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "116_add_workable_stage_local_write_at"
down_revision = "115_prescreen_auto_threshold_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column(
            "workable_stage_local_write_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "workable_stage_local_write_at")
