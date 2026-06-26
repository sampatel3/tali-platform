"""Requisition conversational intake fields.

Additive columns for the redesigned, chat-based requisition intake:
  * ``role_briefs.custom_fields`` — org-template-added field values that have no
    RoleBrief column (JSON object, default {}).
  * ``role_briefs.messages`` — the conversation transcript (JSON array of
    {role, content, attachments}, default []).
  * ``organizations.requisition_spec_template`` — the org's canonical "complete
    requisition spec" definition the chat captures against (JSON, nullable;
    NULL = use the built-in DEFAULT_REQUISITION_TEMPLATE).

Defaults match the model columns (server_default "{}"/"[]" on the role_briefs
columns; nullable, no default, on the org column). No indexes — none of the new
columns are indexed in the model.

Revision ID: 123_add_requisition_chat_fields
Revises: 122_add_role_briefs
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "123_add_requisition_chat_fields"
down_revision = "122_add_role_briefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_briefs",
        sa.Column(
            "custom_fields",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "role_briefs",
        sa.Column(
            "messages",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("requisition_spec_template", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "requisition_spec_template")
    op.drop_column("role_briefs", "messages")
    op.drop_column("role_briefs", "custom_fields")
