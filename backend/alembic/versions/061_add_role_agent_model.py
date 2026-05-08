"""Per-role Anthropic model override.

Adds ``roles.agent_model`` (nullable string). When set, the autonomous
agent uses that model id; when null, the orchestrator falls back to
``settings.resolved_claude_model`` (Haiku by default). Lets recruiters
opt borderline-judgment roles into Sonnet without flipping the global
env var, and keeps cheap triage roles on Haiku.

Revision ID: 061_add_role_agent_model
Revises: 060_add_role_cohort_signals
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "061_add_role_agent_model"
down_revision = "060_add_role_cohort_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("agent_model", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "agent_model")
