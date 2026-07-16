from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "182_workspace_pause_compatibility_audit.py"
    )
    spec = importlib.util.spec_from_file_location("workspace_pause_compat_182", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(engine: sa.Engine):
    metadata = sa.MetaData()
    organizations = sa.Table(
        "organizations",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_workspace_paused_at", sa.DateTime(timezone=True)),
        sa.Column("agent_workspace_paused_reason", sa.Text),
        sa.Column("agent_workspace_paused_by_user_id", sa.Integer),
        sa.Column("agent_workspace_paused_by_name", sa.String(200)),
        sa.Column(
            "agent_workspace_control_version",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
    )
    role_events = sa.Table(
        "role_change_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("changes", sa.JSON, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    workspace_events = sa.Table(
        "workspace_agent_control_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("actor_name", sa.String(200)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("from_version", sa.Integer, nullable=False),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('paused', 'resumed')",
            name="ck_workspace_agent_control_events_action",
        ),
        sa.CheckConstraint(
            "from_version >= 1 AND to_version > from_version",
            name="ck_workspace_agent_control_events_version_advances",
        ),
    )
    metadata.create_all(engine)
    return organizations, role_events, workspace_events


def _run_upgrade(connection: sa.Connection) -> None:
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def _published_role_event(*, event_id: int, organization_id: int, created_at: datetime):
    return {
        "id": event_id,
        "organization_id": organization_id,
        "role_id": 41,
        "actor_user_id": 7,
        "action": "agent_paused",
        "changes": {
            "agent_paused_at": {
                "before": None,
                "after": "2026-07-16T09:00:00+00:00",
            },
            "agent_paused_reason": {
                "before": None,
                "after": "paused by workspace control",
            },
        },
        "reason": "workspace pause migrated to role bulk control",
        "request_id": None,
        "created_at": created_at,
    }


def test_exact_published_evidence_adds_only_compatibility_history():
    engine = sa.create_engine("sqlite://")
    organizations, role_events, workspace_events = _tables(engine)
    conversion_at = datetime(2026, 7, 16, 10, tzinfo=timezone.utc)
    source_event = _published_role_event(
        event_id=10,
        organization_id=1,
        created_at=conversion_at,
    )

    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {"id": 1, "agent_workspace_control_version": 7},
        )
        connection.execute(role_events.insert(), source_event)
        source_snapshot = connection.execute(
            sa.select(role_events).where(role_events.c.id == 10)
        ).mappings().one()

        _run_upgrade(connection)

        organization = connection.execute(sa.select(organizations)).mappings().one()
        retained_source = connection.execute(
            sa.select(role_events).where(role_events.c.id == 10)
        ).mappings().one()
        compatibility_event = connection.execute(
            sa.select(workspace_events)
        ).mappings().one()
        audit = connection.execute(
            sa.text("SELECT * FROM workspace_pause_migration_audits")
        ).mappings().one()

    assert retained_source == source_snapshot
    assert organization["agent_workspace_control_version"] == 8
    assert organization["agent_workspace_paused_at"] is None
    assert compatibility_event["action"] == "migrated"
    assert compatibility_event["actor_user_id"] is None
    assert compatibility_event["actor_name"] == "Taali migration"
    assert compatibility_event["from_version"] == 7
    assert compatibility_event["to_version"] == 8
    assert compatibility_event["request_id"] == (
        "migration:182_workspace_pause_compat_audit:1"
    )
    assert audit["evidence_source"] == "published_175_role_events"
    assert audit["evidence_quality"] == "exact"
    assert audit["converted_role_count"] == 1
    assert json.loads(audit["source_role_event_ids"]) == [10]
    assert json.loads(audit["source_role_ids"]) == [41]
    assert bool(audit["compatibility_applied"]) is True
    assert audit["control_version_before"] == 7
    assert audit["control_version_after"] == 8
    assert json.loads(audit["anomalies"]) == []


def test_later_workspace_action_remains_latest_and_is_not_version_bumped():
    engine = sa.create_engine("sqlite://")
    organizations, role_events, workspace_events = _tables(engine)
    conversion_at = datetime(2026, 7, 16, 10, tzinfo=timezone.utc)
    later_at = datetime(2026, 7, 16, 11, tzinfo=timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {"id": 2, "agent_workspace_control_version": 8},
        )
        connection.execute(
            role_events.insert(),
            _published_role_event(
                event_id=20,
                organization_id=2,
                created_at=conversion_at,
            ),
        )
        connection.execute(
            workspace_events.insert(),
            {
                "id": 30,
                "organization_id": 2,
                "actor_user_id": 8,
                "actor_name": "Aisha Khan",
                "action": "resumed",
                "from_version": 7,
                "to_version": 8,
                "reason": "workspace resumed by recruiter",
                "request_id": "human-action",
                "created_at": later_at,
            },
        )

        _run_upgrade(connection)

        version = connection.execute(
            sa.select(organizations.c.agent_workspace_control_version)
        ).scalar_one()
        events = connection.execute(
            sa.select(workspace_events).order_by(workspace_events.c.id)
        ).mappings().all()
        audit = connection.execute(
            sa.text("SELECT * FROM workspace_pause_migration_audits")
        ).mappings().one()

    assert version == 8
    assert [row["action"] for row in events] == ["resumed"]
    assert bool(audit["compatibility_applied"]) is False
    assert audit["control_version_before"] == 8
    assert audit["control_version_after"] == 8
    assert "later_workspace_action_already_recorded" in json.loads(audit["anomalies"])


def test_migration_172_marker_is_recorded_only_as_limited_evidence():
    engine = sa.create_engine("sqlite://")
    organizations, _role_events, workspace_events = _tables(engine)

    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {"id": 3, "agent_workspace_control_version": 2},
        )
        connection.execute(
            workspace_events.insert(),
            {
                "id": 40,
                "organization_id": 3,
                "actor_user_id": 9,
                "actor_name": "Original Owner",
                "action": "paused",
                "from_version": 1,
                "to_version": 2,
                "reason": "workspace pause migrated from prior bulk control",
                "request_id": "migration:172_workspace_agent_control",
                "created_at": datetime(2026, 7, 15, 10, tzinfo=timezone.utc),
            },
        )

        _run_upgrade(connection)

        version = connection.execute(
            sa.select(organizations.c.agent_workspace_control_version)
        ).scalar_one()
        events = connection.execute(sa.select(workspace_events)).mappings().all()
        audit = connection.execute(
            sa.text("SELECT * FROM workspace_pause_migration_audits")
        ).mappings().one()

    assert version == 2
    assert [row["action"] for row in events] == ["paused"]
    assert audit["evidence_source"] == "migration_172_workspace_event"
    assert audit["evidence_quality"] == "limited"
    assert audit["converted_role_count"] == 0
    assert bool(audit["compatibility_applied"]) is False
    assert audit["source_workspace_event_id"] == 40
    assert "no_published_175_role_event" in json.loads(audit["anomalies"])


def test_fresh_database_fabricates_no_compatibility_rows_or_events():
    engine = sa.create_engine("sqlite://")
    organizations, _role_events, workspace_events = _tables(engine)

    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {"id": 4, "agent_workspace_control_version": 1},
        )
        _run_upgrade(connection)

        version = connection.execute(
            sa.select(organizations.c.agent_workspace_control_version)
        ).scalar_one()
        event_count = connection.execute(
            sa.select(sa.func.count()).select_from(workspace_events)
        ).scalar_one()
        audit_count = connection.execute(
            sa.text("SELECT COUNT(*) FROM workspace_pause_migration_audits")
        ).scalar_one()

    assert version == 1
    assert event_count == 0
    assert audit_count == 0


def test_unsupported_dialect_is_rejected_before_any_ddl():
    migration = _load_migration()
    migration.op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="unsupported-test-dialect")
        )
    )

    with pytest.raises(RuntimeError, match="refusing to partially apply"):
        migration.upgrade()
