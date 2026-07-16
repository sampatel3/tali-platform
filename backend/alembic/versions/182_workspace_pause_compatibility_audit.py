"""Add non-destructive evidence for published workspace-pause conversion.

Revision ID: 182_workspace_pause_compat_audit
Revises: 181_merge_workspace_bulk_role_pause
Create Date: 2026-07-16
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "182_workspace_pause_compat_audit"
down_revision = "181_merge_workspace_bulk_role_pause"
branch_labels = None
depends_on = None

_AUDIT_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_PUBLISHED_REASON = "workspace pause migrated to role bulk control"
_PUBLISHED_ROLE_PAUSE_REASON = "paused by workspace control"
_LEGACY_172_REASON = "workspace pause migrated from prior bulk control"
_LEGACY_172_REQUEST_ID = "migration:172_workspace_agent_control"
_MIGRATION_ACTOR_NAME = "Taali migration"
_SUPPORTED_DIALECTS = frozenset({"postgresql", "sqlite"})


def _require_supported_dialect() -> str:
    dialect = str(op.get_bind().dialect.name)
    if dialect not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "Revision 182 supports only PostgreSQL and SQLite; refusing to "
            f"partially apply compatibility DDL on {dialect!r}."
        )
    return dialect


def _fence_workspace_control_writers() -> None:
    """Take the runtime's organization-first lock before event-table DDL.

    Workspace controls lock an organization row before appending their event.
    Without this table fence, the CHECK replacement could lock the event table
    while a live request holds the organization row, then deadlock when this
    revision later locks that same row to advance its compatibility version.
    """

    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE organizations IN EXCLUSIVE MODE")


def _expand_workspace_action_constraint() -> None:
    """Allow a truthful automated conversion event without weakening checks."""

    connection = op.get_bind()
    expression = "action IN ('paused', 'resumed', 'migrated')"
    canonical = "ck_workspace_agent_control_events_action"
    temporary = "ck_workspace_agent_control_events_action_v182"
    if connection.dialect.name == "postgresql":
        # Keep the original constraint active until its validated replacement
        # is ready, so even concurrent raw writers never see an unchecked gap.
        op.execute(
            sa.text(
                f"ALTER TABLE workspace_agent_control_events "
                f"ADD CONSTRAINT {temporary} CHECK ({expression}) NOT VALID"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE workspace_agent_control_events "
                f"VALIDATE CONSTRAINT {temporary}"
            )
        )
        op.drop_constraint(
            canonical,
            "workspace_agent_control_events",
            type_="check",
        )
        op.execute(
            sa.text(
                f"ALTER TABLE workspace_agent_control_events "
                f"RENAME CONSTRAINT {temporary} TO {canonical}"
            )
        )
        return

    if connection.dialect.name == "sqlite":
        # SQLite cannot alter a CHECK in place. Alembic's batch operation
        # copies every existing row into the validated replacement table.
        with op.batch_alter_table(
            "workspace_agent_control_events",
            recreate="always",
        ) as batch:
            batch.drop_constraint(canonical, type_="check")
            batch.create_check_constraint(canonical, expression)
        return


def _create_audit_table() -> None:
    op.create_table(
        "workspace_pause_migration_audits",
        sa.Column("id", _AUDIT_ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("migration_revision", sa.String(length=64), nullable=False),
        sa.Column("evidence_source", sa.String(length=64), nullable=False),
        sa.Column("evidence_quality", sa.String(length=24), nullable=False),
        sa.Column("converted_role_count", sa.Integer(), nullable=False),
        sa.Column("source_role_event_ids", sa.JSON(), nullable=False),
        sa.Column("source_role_ids", sa.JSON(), nullable=False),
        sa.Column("source_workspace_event_id", _AUDIT_ID_TYPE, nullable=True),
        sa.Column("recorded_workspace_event_id", _AUDIT_ID_TYPE, nullable=True),
        sa.Column("compatibility_applied", sa.Boolean(), nullable=False),
        sa.Column("control_version_before", sa.Integer(), nullable=False),
        sa.Column("control_version_after", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("anomalies", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "converted_role_count >= 0",
            name="ck_workspace_pause_migration_audits_role_count",
        ),
        sa.CheckConstraint(
            "control_version_before >= 1 "
            "AND control_version_after >= control_version_before",
            name="ck_workspace_pause_migration_audits_versions",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "migration_revision",
            name="uq_workspace_pause_migration_audits_org_revision",
        ),
    )
    for name, columns in (
        ("ix_workspace_pause_migration_audits_organization_id", ["organization_id"]),
        ("ix_workspace_pause_migration_audits_source_workspace_event_id", ["source_workspace_event_id"]),
        ("ix_workspace_pause_migration_audits_recorded_workspace_event_id", ["recorded_workspace_event_id"]),
        ("ix_workspace_pause_migration_audits_created_at", ["created_at"]),
        ("ix_workspace_pause_migration_audits_org_created", ["organization_id", "created_at"]),
    ):
        op.create_index(name, "workspace_pause_migration_audits", columns)


def _json_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _timestamp(value: object) -> datetime | None:
    parsed: datetime | None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _published_event(row: sa.RowMapping) -> bool:
    changes = _json_mapping(row["changes"])
    pause_reason_change = (
        changes.get("agent_paused_reason") if changes is not None else None
    )
    return bool(
        isinstance(pause_reason_change, dict)
        and pause_reason_change.get("after") == _PUBLISHED_ROLE_PAUSE_REASON
    )


def _pause_timestamp(row: sa.RowMapping) -> object:
    changes = _json_mapping(row["changes"])
    paused_at_change = changes.get("agent_paused_at") if changes is not None else None
    if not isinstance(paused_at_change, dict):
        return None
    return paused_at_change.get("after")


def _record_compatibility_evidence() -> None:
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
    role_events = sa.table(
        "role_change_events",
        sa.column("id", sa.BigInteger),
        sa.column("organization_id", sa.Integer),
        sa.column("role_id", sa.Integer),
        sa.column("actor_user_id", sa.Integer),
        sa.column("action", sa.String(64)),
        sa.column("changes", sa.JSON),
        sa.column("reason", sa.Text),
        sa.column("request_id", sa.String(128)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    workspace_events = sa.table(
        "workspace_agent_control_events",
        sa.column("id", _AUDIT_ID_TYPE),
        sa.column("organization_id", sa.Integer),
        sa.column("actor_user_id", sa.Integer),
        sa.column("actor_name", sa.String(200)),
        sa.column("action", sa.String(16)),
        sa.column("from_version", sa.Integer),
        sa.column("to_version", sa.Integer),
        sa.column("reason", sa.Text),
        sa.column("request_id", sa.String(128)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    audit_rows = sa.table(
        "workspace_pause_migration_audits",
        sa.column("organization_id", sa.Integer),
        sa.column("migration_revision", sa.String(64)),
        sa.column("evidence_source", sa.String(64)),
        sa.column("evidence_quality", sa.String(24)),
        sa.column("converted_role_count", sa.Integer),
        sa.column("source_role_event_ids", sa.JSON),
        sa.column("source_role_ids", sa.JSON),
        sa.column("source_workspace_event_id", _AUDIT_ID_TYPE),
        sa.column("recorded_workspace_event_id", _AUDIT_ID_TYPE),
        sa.column("compatibility_applied", sa.Boolean),
        sa.column("control_version_before", sa.Integer),
        sa.column("control_version_after", sa.Integer),
        sa.column("provenance", sa.JSON),
        sa.column("anomalies", sa.JSON),
    )

    possible_role_rows = connection.execute(
        sa.select(
            role_events.c.id,
            role_events.c.organization_id,
            role_events.c.role_id,
            role_events.c.actor_user_id,
            role_events.c.changes,
            role_events.c.created_at,
        )
        .where(
            role_events.c.action == "agent_paused",
            role_events.c.reason == _PUBLISHED_REASON,
            role_events.c.request_id.is_(None),
        )
        .order_by(role_events.c.organization_id, role_events.c.id)
    ).mappings().all()
    exact_by_org: dict[int, list[sa.RowMapping]] = defaultdict(list)
    for row in possible_role_rows:
        if _published_event(row):
            exact_by_org[int(row["organization_id"])].append(row)

    legacy_rows = connection.execute(
        sa.select(
            workspace_events.c.id,
            workspace_events.c.organization_id,
            workspace_events.c.actor_user_id,
            workspace_events.c.actor_name,
            workspace_events.c.from_version,
            workspace_events.c.to_version,
            workspace_events.c.created_at,
        )
        .where(
            workspace_events.c.action == "paused",
            workspace_events.c.reason == _LEGACY_172_REASON,
            workspace_events.c.request_id == _LEGACY_172_REQUEST_ID,
        )
        .order_by(workspace_events.c.organization_id, workspace_events.c.id)
    ).mappings().all()
    legacy_by_org = {
        int(row["organization_id"]): row
        for row in legacy_rows
    }

    candidate_org_ids = sorted(set(exact_by_org) | set(legacy_by_org))
    for organization_id in candidate_org_ids:
        exact_rows = exact_by_org.get(organization_id, [])
        legacy_row = legacy_by_org.get(organization_id)
        if not exact_rows and legacy_row is None:
            continue

        org_query = sa.select(organizations).where(
            organizations.c.id == organization_id
        )
        if connection.dialect.name == "postgresql":
            org_query = org_query.with_for_update()
        organization = connection.execute(org_query).mappings().one_or_none()

        latest_workspace_event = connection.execute(
            sa.select(workspace_events)
            .where(workspace_events.c.organization_id == organization_id)
            .order_by(workspace_events.c.id.desc())
            .limit(1)
        ).mappings().one_or_none()

        before_version = int(
            (organization or {}).get("agent_workspace_control_version") or 1
        )
        after_version = before_version
        recorded_workspace_event_id: int | None = None
        anomalies: list[str] = []
        compatibility_applied = False

        if exact_rows:
            evidence_source = "published_175_role_events"
            evidence_quality = "exact"
            source_role_event_ids = [int(row["id"]) for row in exact_rows]
            source_role_ids = [int(row["role_id"]) for row in exact_rows]
            actor_ids = sorted(
                {
                    int(row["actor_user_id"])
                    for row in exact_rows
                    if row["actor_user_id"] is not None
                }
            )
            pause_timestamps = sorted(
                {
                    str(value)
                    for value in (_pause_timestamp(row) for row in exact_rows)
                    if value is not None
                }
            )
            conversion_timestamps = [
                timestamp
                for timestamp in (_timestamp(row["created_at"]) for row in exact_rows)
                if timestamp is not None
            ]
            cutoff = min(conversion_timestamps) if conversion_timestamps else None
            if not conversion_timestamps:
                anomalies.append("conversion_timestamp_unavailable")
            if len(actor_ids) > 1:
                anomalies.append("multiple_source_actor_ids")
            provenance: dict[str, Any] = {
                "source_actor_user_ids": actor_ids,
                "source_paused_at_values": pause_timestamps,
            }
            source_workspace_event_id = None

            later_workspace_action = True
            if cutoff is not None:
                later_workspace_action = connection.execute(
                    sa.select(
                        sa.exists().where(
                            workspace_events.c.organization_id == organization_id,
                            workspace_events.c.created_at >= cutoff,
                            sa.or_(
                                workspace_events.c.request_id.is_(None),
                                ~workspace_events.c.request_id.startswith(
                                    f"migration:{revision}:"
                                ),
                            ),
                        )
                    )
                ).scalar_one()
            if later_workspace_action:
                anomalies.append("later_workspace_action_already_recorded")
            overlay_present = bool(
                organization is not None
                and organization["agent_workspace_paused_at"] is not None
            )
            if overlay_present:
                anomalies.append("workspace_overlay_present")
            if organization is None:
                anomalies.append("organization_missing")

            if (
                organization is not None
                and cutoff is not None
                and not later_workspace_action
                and not overlay_present
            ):
                after_version = before_version + 1
                guarded_update = (
                    organizations.update()
                    .where(
                        organizations.c.id == organization_id,
                        organizations.c.agent_workspace_control_version
                        == before_version,
                        organizations.c.agent_workspace_paused_at.is_(None),
                        ~sa.exists().where(
                            workspace_events.c.organization_id == organization_id,
                            workspace_events.c.created_at >= cutoff,
                        ),
                    )
                    .values(agent_workspace_control_version=after_version)
                )
                if connection.execute(guarded_update).rowcount == 1:
                    recorded_workspace_event_id = int(
                        connection.execute(
                            workspace_events.insert()
                            .values(
                                organization_id=organization_id,
                                actor_user_id=None,
                                actor_name=_MIGRATION_ACTOR_NAME,
                                action="migrated",
                                from_version=before_version,
                                to_version=after_version,
                                reason=(
                                    "Automated compatibility record for published "
                                    "workspace-pause conversion; no role was resumed "
                                    "or otherwise changed by this revision."
                                ),
                                request_id=f"migration:{revision}:{organization_id}",
                            )
                            .returning(workspace_events.c.id)
                        ).scalar_one()
                    )
                    compatibility_applied = True
                else:
                    after_version = before_version
                    anomalies.append("concurrent_workspace_action_won")
        else:
            if (
                latest_workspace_event is None
                or int(latest_workspace_event["id"]) != int(legacy_row["id"])
            ):
                # A subsequent control action supersedes this limited marker;
                # without any exact 175 role event, recording a conversion
                # would overstate what the surviving data can prove.
                continue
            evidence_source = "migration_172_workspace_event"
            evidence_quality = "limited"
            source_role_event_ids = []
            source_role_ids = []
            source_workspace_event_id = int(legacy_row["id"])
            provenance = {
                "source_actor_user_id": legacy_row["actor_user_id"],
                "source_actor_name": legacy_row["actor_name"],
                "source_event_created_at": str(legacy_row["created_at"]),
            }
            anomalies.append("no_published_175_role_event")
            if organization is None:
                anomalies.append("organization_missing")

        connection.execute(
            audit_rows.insert().values(
                organization_id=organization_id,
                migration_revision=revision,
                evidence_source=evidence_source,
                evidence_quality=evidence_quality,
                converted_role_count=len(exact_rows),
                source_role_event_ids=source_role_event_ids,
                source_role_ids=source_role_ids,
                source_workspace_event_id=source_workspace_event_id,
                recorded_workspace_event_id=recorded_workspace_event_id,
                compatibility_applied=compatibility_applied,
                control_version_before=before_version,
                control_version_after=after_version,
                provenance=provenance,
                anomalies=anomalies,
            )
        )


def _protect_audit_table() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION reject_workspace_pause_migration_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'workspace_pause_migration_audits is append-only';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER workspace_pause_migration_audits_append_only
        BEFORE UPDATE OR DELETE ON workspace_pause_migration_audits
        FOR EACH ROW EXECUTE FUNCTION reject_workspace_pause_migration_audit_mutation();
        """
    )


def upgrade() -> None:
    _require_supported_dialect()
    _fence_workspace_control_writers()
    _create_audit_table()
    _expand_workspace_action_constraint()
    _record_compatibility_evidence()
    _protect_audit_table()


def downgrade() -> None:
    raise RuntimeError(
        "Revision 182 is intentionally irreversible: downgrading would delete "
        "append-only compatibility evidence or invalidate migrated events."
    )
