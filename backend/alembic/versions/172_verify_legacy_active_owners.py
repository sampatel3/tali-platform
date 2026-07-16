"""Grandfather active workspace owners before enforcing verified login.

Revision ID: 172_verify_legacy_active_owners
Revises: 172_workspace_agent_control
Create Date: 2026-07-15

Historically the auth router allowed unverified login and the UI treated owners
as active. Marking that already-authorized cohort verified avoids a deployment
lockout; all registrations after this migration must complete email
verification because the auth router now requires it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "172_verify_legacy_active_owners"
down_revision = "172_workspace_agent_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    users = sa.table(
        "users",
        sa.column("role", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("is_verified", sa.Boolean()),
    )
    op.execute(
        users.update()
        .where(users.c.role == "owner")
        .where(users.c.is_active.is_(True))
        .where(users.c.is_verified.is_(False))
        .values(is_verified=True)
    )


def downgrade() -> None:
    # Verification is evidence-like state and cannot safely be inferred away.
    pass
