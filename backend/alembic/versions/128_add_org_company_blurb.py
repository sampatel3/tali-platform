"""Cached, auto-derived "About the company" blurb on the organization.

The role-agnostic company description is the same across every job spec, so it's
derived ONCE (a cheap LLM extraction over recent role specs, stripped of
role-specific content) and cached here, then copied onto each new requisition's
spec. NULL = not yet derived; "" = derived but nothing found (don't re-call).

Adds:
  * ``organizations.company_blurb`` — Text, nullable.

Revision ID: 128_add_org_company_blurb
Revises: 127_add_requisition_job_bridge
Create Date: 2026-06-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "128_add_org_company_blurb"
down_revision = "127_add_requisition_job_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("company_blurb", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "company_blurb")
