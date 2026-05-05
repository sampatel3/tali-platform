"""Unify per-role monthly budget across all features.

Two changes:

1. ``roles.agent_usd_budget_monthly_cents`` is renamed to
   ``monthly_usd_budget_cents``. The column was added in 056 as an
   agent-only cap, but the platform decision is that *all* Anthropic
   spend on a role (scoring, pre-screen, assessment, agent) should feed
   into a single monthly cap. The rename makes the semantics correct.
   No production data exists in the column yet — no role has
   ``agentic_mode_enabled = true``, so the rename is a clean operation.

2. ``usage_events`` gets a nullable ``role_id`` foreign key + index so
   the role-level budget query can sum across features without joining
   through applications/assessments. Existing rows stay nullable; new
   rows populated by ``record_event`` callers that have role context.

Revision ID: 057_unify_role_monthly_budget
Revises: 056_add_agentic_recruiting
Create Date: 2026-05-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "057_unify_role_monthly_budget"
down_revision = "056_add_agentic_recruiting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "roles",
        "agent_usd_budget_monthly_cents",
        new_column_name="monthly_usd_budget_cents",
    )

    op.add_column(
        "usage_events",
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_usage_events_role_created",
        "usage_events",
        ["role_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_role_created", table_name="usage_events")
    op.drop_column("usage_events", "role_id")
    op.alter_column(
        "roles",
        "monthly_usd_budget_cents",
        new_column_name="agent_usd_budget_monthly_cents",
    )
