"""Separate hiring-manager intake transcript on the role brief.

The public /intake/{token} link must NOT show the recruiter's raw chat
(``role_briefs.messages``) — that may contain confidential internal context.
The hiring manager gets their OWN conversation thread here; both sides still
fill the same structured brief fields, so captured context informs the agent.

Adds:
  * ``role_briefs.client_messages`` — JSON, NOT NULL, server_default '[]'.

Revision ID: 131_add_role_brief_client_messages
Revises: 130_drop_assessment_cli_session_fields
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "131_add_role_brief_client_messages"
down_revision = "130_drop_assessment_cli_session_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_briefs",
        sa.Column(
            "client_messages",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("role_briefs", "client_messages")
