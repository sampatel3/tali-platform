"""Drop the two legacy workspace-intent columns now that the chip API +
chip composer are live (PR #101 backend, PR #103 frontend).

Both columns were kept populated as a server-side mirror of the chip
state during the transition window so any reader still consuming them
wouldn't break. After PR #103, every Tali surface — Settings → AI agent
in the UI, role create flow, Workable import, organization GET response —
goes through ``org_criteria`` rows or :func:`render_org_intent_block`
helper. Nothing reads either column anymore.

Columns dropped:

- ``organizations.default_role_requirements`` (JSON, the legacy chip-list
  proxy)
- ``organizations.default_additional_requirements`` (Text, the older
  free-text default that fed the role create fallback)

``Role.additional_requirements`` is **not** dropped here — that column
still has ~16 active readers across the agent prompt path / scoring
helpers / interview prompts / MCP payloads / sub-agents that need to
be migrated case-by-case in a follow-up PR. The role-side mirror in
``role_criteria_service.mirror_role_text_from_criteria`` keeps it in
sync with chip state so those readers see the bucketed view.

Revision ID: 067_drop_legacy_org_intent_columns
Revises: 066_add_bucketed_criteria
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "067_drop_legacy_org_intent_columns"
down_revision = "066_add_bucketed_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("organizations", "default_role_requirements")
    op.drop_column("organizations", "default_additional_requirements")


def downgrade() -> None:
    # Re-add as nullable so a rollback doesn't fail on existing rows. The
    # data is gone — recovering it would mean re-deriving from
    # ``org_criteria`` chips, which is out of scope for a downgrade.
    op.add_column(
        "organizations",
        sa.Column("default_additional_requirements", sa.Text, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("default_role_requirements", sa.JSON, nullable=True),
    )
