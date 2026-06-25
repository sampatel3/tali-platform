"""P0.5: RBAC — users.role.

Adds a role column (admin | recruiter | hiring_manager | interviewer | viewer),
default 'admin' so existing users are unchanged (they were all effectively admin
pre-RBAC). The permission dependency (deps.require_role) gates write endpoints
that opt in; broad write-route gating is a follow-up.

Revision ID: 123_add_user_role
Revises: 122_audit_event_immutability
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "123_add_user_role"
down_revision = "122_audit_event_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(), nullable=False, server_default="admin"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
