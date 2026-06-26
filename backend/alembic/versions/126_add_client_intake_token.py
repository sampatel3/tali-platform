"""Client intake share-link token on role_briefs.

A consultancy recruiter mints a SCOPED, no-login share link for their CLIENT,
who describes the role they need via the same conversational intake agent —
but the company/economics layers are hidden and the agent never asks about
pay. The link is addressed by an unguessable ``client_intake_token``.

Adds:
  * ``role_briefs.client_intake_token`` — String, unique, indexed, nullable
    (null until the recruiter mints the link; minted once and reused).

The index is emitted explicitly via op.create_index (mirrors 125_add_job_pages
— ``index=True`` on the model column does not auto-create the index here).

Revision ID: 126_add_client_intake_token
Revises: 125_add_job_pages
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "126_add_client_intake_token"
down_revision = "125_add_job_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_briefs",
        sa.Column("client_intake_token", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_role_briefs_client_intake_token",
        "role_briefs",
        ["client_intake_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_role_briefs_client_intake_token", table_name="role_briefs"
    )
    op.drop_column("role_briefs", "client_intake_token")
