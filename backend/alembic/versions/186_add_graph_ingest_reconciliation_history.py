"""Retain owner-attested graph-ingest reconciliation evidence.

Revision ID: 186_graph_ingest_reconciliation
Revises: 185_graph_ingest_dispatch
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "186_graph_ingest_reconciliation"
down_revision = "185_graph_ingest_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_ingest_dispatches",
        sa.Column("reconciliation_history", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_graph_ingest_dispatches_reconciliation",
        "graph_ingest_dispatches",
        ["organization_id", "status", "completed_at", "operation_id"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 186 is intentionally irreversible: graph-ingest "
        "reconciliation history is operational evidence and must not be deleted."
    )
