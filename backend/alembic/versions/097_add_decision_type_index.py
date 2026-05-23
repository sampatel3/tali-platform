"""Add a composite index covering the Decision Hub type filter.

The hot path is ``GET /api/v1/agent-decisions`` (the Review queue /
Decision feed). It always filters ``(organization_id, status)`` and,
when a type pill is selected, adds ``decision_type IN (...)``, sorting by
``created_at DESC`` with a small ``LIMIT``.

The existing ``ix_agent_decisions_org_status_created`` =
``(organization_id, status, created_at)`` serves the default (no-type)
view well, but it does NOT cover ``decision_type``. When a sparse type is
selected (e.g. "Advance" amid a queue dominated by pre-screen rejects),
Postgres walks the entire ``(org, status)`` pending population in
created_at order post-filtering ``decision_type`` row by row to fill the
LIMIT — effectively a full scan of pending for that org.

Adding ``decision_type`` ahead of ``created_at`` lets the planner range-
scan each requested type in created_at order (MergeAppend across the IN
list) and stop at the LIMIT, so a sparse-type filter is as fast as the
common one.

Plain b-tree, ``IF NOT EXISTS`` for idempotency. At current table size
the build is sub-second; the brief lock on deploy is negligible.

Revision ID: 097_add_decision_type_index
Revises: 096_add_role_star_auto_managed
Create Date: 2026-05-23
"""

from __future__ import annotations

from alembic import op


revision = "097_add_decision_type_index"
down_revision = "096_add_role_star_auto_managed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_decisions_org_status_type_created",
        "agent_decisions",
        ["organization_id", "status", "decision_type", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_decisions_org_status_type_created",
        table_name="agent_decisions",
        if_exists=True,
    )
