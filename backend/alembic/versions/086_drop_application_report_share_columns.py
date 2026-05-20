"""Drop legacy single-token share-link columns from ``candidate_applications``.

Replaced by the multi-link ``share_links`` table (migration 059). The
column-based system never had a working SPA route — recipient links
generated against ``report_share_token`` landed on ``/share/:token``
which had no handler, so the column was dead weight. Drop it now that
the new public ``GET /share/{token}`` endpoint returns the full
application payload directly out of the ``share_links`` row.

Revision ID: 086_drop_report_share_columns
Revises: 085_rename_ti_to_advanced
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "086_drop_report_share_columns"
down_revision = "085_rename_ti_to_advanced"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_candidate_applications_report_share_token",
        table_name="candidate_applications",
    )
    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("report_share_created_at")
        batch.drop_column("report_share_token")


def downgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("report_share_token", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("report_share_created_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_index(
        "ix_candidate_applications_report_share_token",
        "candidate_applications",
        ["report_share_token"],
        unique=True,
    )
