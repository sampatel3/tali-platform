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


def _iso(value: object) -> str | None:
    if value is None:
        return None
    formatter = getattr(value, "isoformat", None)
    return str(formatter() if callable(formatter) else value)


def _provenance(row: sa.RowMapping) -> dict[str, object]:
    return {
        "paused_at": _iso(row["paused_at"]),
        "reason": row["pause_reason"],
        "actor_user_id": row["actor_user_id"],
        "actor_name": row["actor_name"],
    }


def _migration_reason(row: sa.RowMapping) -> str:
    original = _provenance(row)
    return (
        "Legacy workspace pause converted to independently resumable role pauses; "
        "the actor fields identify the original pause, while this conversion was "
        f"automated. Original provenance: {original}"
    )


def upgrade() -> None:
    """Preserve effective pauses and their provenance without overwriting races."""

    connection = op.get_bind()
    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Integer),
        sa.column("agent_workspace_paused_at", sa.DateTime(timezone=True)),
        sa.column("agent_workspace_paused_reason", sa.Text),
        sa.column("agent_workspace_paused_by_user_id", sa.Integer),
        sa.column("agent_workspace_paused_by_name", sa.String(200)),
        sa.column("agent_workspace_control_version", sa.Integer),
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
    workspace_events = sa.table(
        "workspace_agent_control_events",
        sa.column("organization_id", sa.Integer),
        sa.column("actor_user_id", sa.Integer),
        sa.column("actor_name", sa.String(200)),
        sa.column("action", sa.String(16)),
        sa.column("from_version", sa.Integer),
        sa.column("to_version", sa.Integer),
        sa.column("reason", sa.Text),
        sa.column("request_id", sa.String(128)),
    )

    paused_organizations = connection.execute(
        sa.select(
            organizations.c.id.label("organization_id"),
            organizations.c.agent_workspace_paused_at.label("paused_at"),
            organizations.c.agent_workspace_paused_reason.label("pause_reason"),
            organizations.c.agent_workspace_paused_by_user_id.label("actor_user_id"),
            organizations.c.agent_workspace_paused_by_name.label("actor_name"),
            organizations.c.agent_workspace_control_version.label("control_version"),
        )
        .where(organizations.c.agent_workspace_paused_at.isnot(None))
        .order_by(organizations.c.id)
    ).mappings().all()
    if not paused_organizations:
        return
    pause_by_org = {
        int(row["organization_id"]): row for row in paused_organizations
    }

    legacy_roles = connection.execute(
        sa.select(
            roles.c.id.label("role_id"),
            roles.c.organization_id,
            roles.c.version,
        )
        .where(
            roles.c.organization_id.in_(tuple(pause_by_org)),
            roles.c.deleted_at.is_(None),
            roles.c.agentic_mode_enabled.is_(True),
            roles.c.agent_paused_at.is_(None),
        )
        .order_by(roles.c.id)
    ).mappings().all()

    # The literal is historical migration data. Runtime writers import the
    # canonical constant from app.services.agent_pause_reasons.
    bulk_pause_reason = "paused by workspace control"
    for role_row in legacy_roles:
        organization_row = pause_by_org[int(role_row["organization_id"])]
        from_version = int(role_row["version"] or 1)
        to_version = from_version + 1
        update_result = connection.execute(
            roles.update()
            .where(
                roles.c.id == int(role_row["role_id"]),
                roles.c.version == from_version,
                roles.c.deleted_at.is_(None),
                roles.c.agentic_mode_enabled.is_(True),
                roles.c.agent_paused_at.is_(None),
            )
            .values(
                agent_paused_at=organization_row["paused_at"],
                agent_paused_reason=bulk_pause_reason,
                version=to_version,
            )
        )
        if update_result.rowcount != 1:
            current = connection.execute(
                sa.select(
                    roles.c.deleted_at,
                    roles.c.agentic_mode_enabled,
                    roles.c.agent_paused_at,
                ).where(roles.c.id == int(role_row["role_id"]))
            ).mappings().one_or_none()
            if (
                current is None
                or current["deleted_at"] is not None
                or not bool(current["agentic_mode_enabled"])
                or current["agent_paused_at"] is not None
            ):
                continue
            raise RuntimeError(
                "role changed concurrently while converting workspace pause"
            )

        connection.execute(
            role_change_events.insert().values(
                organization_id=int(role_row["organization_id"]),
                role_id=int(role_row["role_id"]),
                actor_user_id=organization_row["actor_user_id"],
                action="agent_paused",
                from_version=from_version,
                to_version=to_version,
                changes={
                    "agent_paused_at": {
                        "before": None,
                        "after": _iso(organization_row["paused_at"]),
                    },
                    "agent_paused_reason": {
                        "before": None,
                        "after": bulk_pause_reason,
                    },
                    "workspace_pause_provenance": _provenance(organization_row),
                },
                reason=_migration_reason(organization_row),
                request_id=None,
            )
        )

    for organization_row in paused_organizations:
        from_version = int(organization_row["control_version"] or 1)
        to_version = from_version + 1
        update_result = connection.execute(
            organizations.update()
            .where(
                organizations.c.id == int(organization_row["organization_id"]),
                organizations.c.agent_workspace_control_version == from_version,
                organizations.c.agent_workspace_paused_at
                == organization_row["paused_at"],
            )
            .values(
                agent_workspace_paused_at=None,
                agent_workspace_paused_reason=None,
                agent_workspace_paused_by_user_id=None,
                agent_workspace_paused_by_name=None,
                agent_workspace_control_version=to_version,
            )
        )
        if update_result.rowcount != 1:
            raise RuntimeError(
                "workspace pause changed concurrently during conversion"
            )
        connection.execute(
            workspace_events.insert().values(
                organization_id=int(organization_row["organization_id"]),
                actor_user_id=organization_row["actor_user_id"],
                actor_name=(
                    str(organization_row["actor_name"])[:200]
                    if organization_row["actor_name"] is not None
                    else None
                ),
                # The overlay is cleared, but its effective paused state is
                # retained on each eligible role. Record the user-visible
                # outcome so stale-command conflicts never claim these agents
                # were resumed by the original recruiter.
                action="paused",
                from_version=from_version,
                to_version=to_version,
                reason=_migration_reason(organization_row),
                request_id=None,
            )
        )


def downgrade() -> None:
    # Intentionally irreversible: reconstructing an organization-wide blocker
    # would override independent role actions made after this conversion.
    pass
