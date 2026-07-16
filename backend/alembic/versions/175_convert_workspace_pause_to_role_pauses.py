"""Convert workspace pause overlays into independently resumable role pauses.

Revision ID: 175_workspace_bulk_role_pause
Revises: 174_related_role_workflow
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "175_workspace_bulk_role_pause"
down_revision = "174_related_role_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the effective state of every active legacy overlay, but move it
    # to the roles so an individual role Resume can run immediately. Existing
    # role-owned manual/system holds are deliberately left unchanged.
    connection = op.get_bind()
    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Integer),
        sa.column("agent_workspace_paused_at", sa.DateTime(timezone=True)),
        sa.column("agent_workspace_paused_reason", sa.Text),
        sa.column("agent_workspace_paused_by_user_id", sa.Integer),
        sa.column("agent_workspace_paused_by_name", sa.String(200)),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer),
        sa.column("organization_id", sa.Integer),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("agentic_mode_enabled", sa.Boolean),
        sa.column("agent_paused_at", sa.DateTime(timezone=True)),
        sa.column("agent_paused_reason", sa.Text),
        sa.column("version", sa.Integer),
    )
    role_change_events = sa.table(
        "role_change_events",
        sa.column("organization_id", sa.Integer),
        sa.column("role_id", sa.Integer),
        sa.column("actor_user_id", sa.Integer),
        sa.column("action", sa.String(64)),
        sa.column("from_version", sa.Integer),
        sa.column("to_version", sa.Integer),
        sa.column("changes", sa.JSON),
        sa.column("reason", sa.Text),
        sa.column("request_id", sa.String(128)),
    )
    legacy_roles = connection.execute(
        sa.select(
            roles.c.id.label("role_id"),
            roles.c.organization_id,
            roles.c.version,
            organizations.c.agent_workspace_paused_at.label("paused_at"),
            organizations.c.agent_workspace_paused_by_user_id.label("actor_user_id"),
        )
        .select_from(
            roles.join(
                organizations,
                roles.c.organization_id == organizations.c.id,
            )
        )
        .where(
            organizations.c.agent_workspace_paused_at.isnot(None),
            roles.c.deleted_at.is_(None),
            roles.c.agentic_mode_enabled.is_(True),
            roles.c.agent_paused_at.is_(None),
        )
        .order_by(roles.c.id)
    ).mappings().all()

    # The literal is historical migration data. Runtime writers import the
    # canonical constant from app.services.agent_pause_reasons.
    bulk_pause_reason = "paused by workspace control"
    for row in legacy_roles:
        from_version = int(row["version"] or 1)
        to_version = from_version + 1
        paused_at = row["paused_at"]
        paused_at_json = paused_at.isoformat() if paused_at is not None else None
        connection.execute(
            roles.update()
            .where(roles.c.id == int(row["role_id"]))
            .values(
                agent_paused_at=paused_at,
                agent_paused_reason=bulk_pause_reason,
                version=to_version,
            )
        )
        connection.execute(
            role_change_events.insert().values(
                organization_id=int(row["organization_id"]),
                role_id=int(row["role_id"]),
                actor_user_id=row["actor_user_id"],
                action="agent_paused",
                from_version=from_version,
                to_version=to_version,
                changes={
                    "agent_paused_at": {"before": None, "after": paused_at_json},
                    "agent_paused_reason": {
                        "before": None,
                        "after": bulk_pause_reason,
                    },
                },
                reason="workspace pause migrated to role bulk control",
                request_id=None,
            )
        )
    op.execute(
        sa.text(
            """
            UPDATE organizations
               SET agent_workspace_paused_at = NULL,
                   agent_workspace_paused_reason = NULL,
                   agent_workspace_paused_by_user_id = NULL,
                   agent_workspace_paused_by_name = NULL
             WHERE agent_workspace_paused_at IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    # This is intentionally irreversible: reconstructing a global blocker from
    # role pauses would override independent role actions made after upgrade.
    pass
