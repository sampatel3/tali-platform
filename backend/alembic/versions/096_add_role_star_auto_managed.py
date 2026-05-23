"""Add ``star_auto_managed`` to ``roles`` and auto-star live (published) jobs.

Live Workable jobs (``state == 'published'``) should always be in continuous
sync. We auto-apply ``starred_for_auto_sync`` to them and mark the star
``star_auto_managed`` so it can be dropped automatically once the job leaves
the published state — without clobbering a recruiter's manual star (which
keeps ``star_auto_managed`` False and is therefore sticky).

The one-time backfill stars the org's currently-published roles so the change
takes effect immediately rather than waiting for the next jobs-only sync tick.

Revision ID: 096_add_role_star_auto_managed
Revises: 095_add_workable_disqualified
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "096_add_role_star_auto_managed"
down_revision = "095_add_workable_disqualified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.add_column(
            sa.Column(
                "star_auto_managed",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            )
        )

    # Backfill: star currently-published Workable roles that aren't starred
    # yet, and mark those stars as auto-managed. Manual stars already present
    # are left untouched (they stay sticky). JSON state extraction is
    # Postgres-specific, so guard on the dialect.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE roles
                SET starred_for_auto_sync = true,
                    star_auto_managed = true
                WHERE source = 'workable'
                  AND deleted_at IS NULL
                  AND starred_for_auto_sync = false
                  AND workable_job_data ->> 'state' = 'published'
                """
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.drop_column("star_auto_managed")
