"""Drop the legacy ``Role.additional_requirements`` text column now that
every reader has migrated to chip-based helpers.

Migration order:

- alembic 066 added the chip schema (``org_criteria`` table, ``bucket``
  + ``org_criterion_id`` on ``role_criteria``)
- alembic 067 dropped the workspace-side legacy columns
  (``Organization.default_role_requirements`` JSON,
  ``Organization.default_additional_requirements`` Text)
- alembic 068 (this) drops the role-side legacy column. Every previous
  consumer (system_prompt, MCP payload + role markdown, interview
  helpers, v3 fit scorer caller in Workable sync + assessment
  submission runtime, automation tasks for interview focus, role
  serializer for ``RoleResponse``) now uses
  :func:`render_role_intent_block` / :func:`render_role_intent_lines`
  from ``services.role_criteria_service``.

API impact (callers should already be on the chip endpoints):

- ``POST /roles`` no longer accepts ``additional_requirements`` in the
  body. Callers manage chips via ``/roles/{id}/criteria``; new roles
  inherit the workspace chip set automatically.
- ``PATCH /roles/{id}`` no longer accepts the field.
- ``GET /roles/{id}`` no longer returns it (the structured ``criteria``
  list has been the actual source for the UI since PR #103).

Revision ID: 068_drop_role_additional_requirements
Revises: 067_drop_legacy_org_intent_columns
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "068_drop_role_additional_requirements"
down_revision = "067_drop_legacy_org_intent_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("roles", "additional_requirements")


def downgrade() -> None:
    # Re-add as nullable so the rollback succeeds on existing rows. The
    # old text data is gone — recovering it would mean joining
    # ``role_criteria`` rows back into a string, which isn't worth doing
    # for a downgrade path.
    op.add_column(
        "roles",
        sa.Column("additional_requirements", sa.Text, nullable=True),
    )
