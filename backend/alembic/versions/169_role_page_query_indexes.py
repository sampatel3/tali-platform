"""Add composite indexes used by the role-detail critical path.

Revision ID: 169_role_page_query_indexes
Revises: 168_bh_cred_generation
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op


revision = "169_role_page_query_indexes"
down_revision = "168_bh_cred_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_decisions_application_status",
        "agent_decisions",
        ["application_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decisions_application_status", table_name="agent_decisions")
