"""users.role — org-level 'owner' | 'member'.

Owners manage members (invite/resend/revoke/remove) and org access settings;
everyone else is a member. Backfill makes the first registered user of each
organization (lowest id) its owner.

Revision ID: 140_add_user_role
Revises: 139_add_application_workable_created_at
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "140_add_user_role"
down_revision = "139_add_application_workable_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
    )
    # Earliest ACTIVE user of each org (lowest id — ids are monotonic) owns it;
    # a disabled first account must not become the only owner, or no active
    # member could pass require_org_owner after the upgrade.
    op.execute(
        """
        UPDATE users SET role = 'owner'
        WHERE id IN (
            SELECT MIN(id) FROM users
            WHERE organization_id IS NOT NULL AND is_active = true
            GROUP BY organization_id
        )
        """
    )
    # Orgs whose every account is disabled still get their earliest user as
    # owner, so ownership is well-defined if the account is ever re-enabled.
    op.execute(
        """
        UPDATE users SET role = 'owner'
        WHERE id IN (
            SELECT MIN(id) FROM users u
            WHERE organization_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM users a
                  WHERE a.organization_id = u.organization_id AND a.is_active = true
              )
            GROUP BY organization_id
        )
        """
    )


def downgrade() -> None:
    op.drop_column("users", "role")
