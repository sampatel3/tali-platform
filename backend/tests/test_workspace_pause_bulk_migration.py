from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "175_convert_workspace_pause_to_role_pauses.py"
    )
    spec = importlib.util.spec_from_file_location("workspace_bulk_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_tables(engine):
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
            "agent_workspace_control_version", sa.Integer, nullable=False
        ),
    )
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("agentic_mode_enabled", sa.Boolean, nullable=False),
        sa.Column("agent_paused_at", sa.DateTime(timezone=True)),
        sa.Column("agent_paused_reason", sa.Text),
        sa.Column("version", sa.Integer, nullable=False),
    )
    role_events = sa.Table(
        "role_change_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("from_version", sa.Integer, nullable=False),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("changes", sa.JSON, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(128)),
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
    )
    metadata.create_all(engine)
    return organizations, roles, role_events, workspace_events


def test_active_workspace_overlay_becomes_independent_role_pauses(monkeypatch):
    engine = sa.create_engine("sqlite://")
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
            "agent_workspace_control_version", sa.Integer, nullable=False
        ),
    )
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("agentic_mode_enabled", sa.Boolean, nullable=False),
        sa.Column("agent_paused_at", sa.DateTime(timezone=True)),
        sa.Column("agent_paused_reason", sa.Text),
        sa.Column("version", sa.Integer, nullable=False),
    )
    role_change_events = sa.Table(
        "role_change_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("from_version", sa.Integer, nullable=False),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("changes", sa.JSON, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(128)),
    )
    workspace_control_events = sa.Table(
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
    )
    metadata.create_all(engine)
    paused_at = datetime(2026, 7, 16, 8, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {
                "id": 1,
                "agent_workspace_paused_at": paused_at,
                "agent_workspace_paused_reason": "workspace paused by recruiter",
                "agent_workspace_paused_by_user_id": 7,
                "agent_workspace_paused_by_name": "Sam Patel",
                "agent_workspace_control_version": 4,
            },
        )
        connection.execute(
            roles.insert(),
            [
                {
                    "id": 1,
                    "organization_id": 1,
                    "agentic_mode_enabled": True,
                    "agent_paused_at": None,
                    "agent_paused_reason": None,
                    "version": 3,
                },
                {
                    "id": 2,
                    "organization_id": 1,
                    "agentic_mode_enabled": True,
                    "agent_paused_at": paused_at,
                    "agent_paused_reason": "monthly USD cap reached",
                    "version": 5,
                },
                {
                    "id": 3,
                    "organization_id": 1,
                    "agentic_mode_enabled": False,
                    "agent_paused_at": None,
                    "agent_paused_reason": None,
                    "version": 2,
                },
            ],
        )
        migration = _load_migration()
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        org = connection.execute(sa.select(organizations)).mappings().one()
        migrated = connection.execute(
            sa.select(roles).order_by(roles.c.id)
        ).mappings().all()
        audit = connection.execute(sa.select(role_change_events)).mappings().one()
        workspace_audit = connection.execute(
            sa.select(workspace_control_events)
        ).mappings().one()

    assert org["agent_workspace_paused_at"] is None
    assert org["agent_workspace_paused_reason"] is None
    assert org["agent_workspace_control_version"] == 5
    assert migrated[0]["agent_paused_at"] is not None
    assert migrated[0]["agent_paused_reason"] == "paused by workspace control"
    assert migrated[0]["version"] == 4
    assert migrated[1]["agent_paused_reason"] == "monthly USD cap reached"
    assert migrated[1]["version"] == 5
    assert migrated[2]["agent_paused_at"] is None
    assert migrated[2]["version"] == 2
    assert audit["organization_id"] == 1
    assert audit["role_id"] == 1
    assert audit["actor_user_id"] == 7
    assert audit["action"] == "agent_paused"
    assert audit["from_version"] == 3
    assert audit["to_version"] == 4
    assert audit["changes"]["agent_paused_reason"]["after"] == "paused by workspace control"
    assert audit["changes"]["workspace_pause_provenance"] == {
        "paused_at": paused_at.replace(tzinfo=None).isoformat(),
        "reason": "workspace paused by recruiter",
        "actor_user_id": 7,
        "actor_name": "Sam Patel",
    }
    assert "Original provenance" in audit["reason"]
    assert workspace_audit["organization_id"] == 1
    assert workspace_audit["actor_user_id"] == 7
    assert workspace_audit["actor_name"] == "Sam Patel"
    assert workspace_audit["action"] == "paused"
    assert workspace_audit["from_version"] == 4
    assert workspace_audit["to_version"] == 5
    assert "workspace paused by recruiter" in workspace_audit["reason"]


def test_overlay_provenance_survives_when_no_role_is_eligible(monkeypatch):
    engine = sa.create_engine("sqlite://")
    organizations, roles, role_events, workspace_events = _migration_tables(engine)
    paused_at = datetime(2026, 7, 16, 10, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {
                "id": 2,
                "agent_workspace_paused_at": paused_at,
                "agent_workspace_paused_reason": "incident hold",
                "agent_workspace_paused_by_user_id": 19,
                "agent_workspace_paused_by_name": "Operations Owner",
                "agent_workspace_control_version": 8,
            },
        )
        connection.execute(
            roles.insert(),
            {
                "id": 20,
                "organization_id": 2,
                "agentic_mode_enabled": False,
                "agent_paused_at": None,
                "agent_paused_reason": None,
                "version": 2,
            },
        )
        migration = _load_migration()
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        organization = connection.execute(
            sa.select(organizations)
        ).mappings().one()
        workspace_audit = connection.execute(
            sa.select(workspace_events)
        ).mappings().one()
        role = connection.execute(sa.select(roles)).mappings().one()
        assert connection.execute(sa.select(role_events)).first() is None

    assert organization["agent_workspace_paused_at"] is None
    assert organization["agent_workspace_control_version"] == 9
    assert role["agent_paused_at"] is None
    assert role["version"] == 2
    assert workspace_audit["actor_user_id"] == 19
    assert workspace_audit["actor_name"] == "Operations Owner"
    assert "incident hold" in workspace_audit["reason"]
    assert paused_at.replace(tzinfo=None).isoformat() in workspace_audit["reason"]


def test_concurrent_independent_role_pause_is_never_overwritten(monkeypatch):
    engine = sa.create_engine("sqlite://")
    organizations, roles, role_events, _workspace_events = _migration_tables(engine)
    paused_at = datetime(2026, 7, 16, 11, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            {
                "id": 3,
                "agent_workspace_paused_at": paused_at,
                "agent_workspace_paused_reason": "workspace hold",
                "agent_workspace_paused_by_user_id": 23,
                "agent_workspace_paused_by_name": "Workspace Owner",
                "agent_workspace_control_version": 11,
            },
        )
        connection.execute(
            roles.insert(),
            {
                "id": 30,
                "organization_id": 3,
                "agentic_mode_enabled": True,
                "agent_paused_at": None,
                "agent_paused_reason": None,
                "version": 3,
            },
        )
        raced = False

        def pause_before_migration_update(
            _connection,
            cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            nonlocal raced
            if not raced and statement.lstrip().upper().startswith("UPDATE ROLES SET"):
                raced = True
                cursor.execute(
                    "UPDATE roles SET agent_paused_at = "
                    "'2026-07-16 11:05:00', "
                    "agent_paused_reason = 'paused independently', "
                    "version = version + 1 WHERE id = 30"
                )

        sa.event.listen(engine, "before_cursor_execute", pause_before_migration_update)
        migration = _load_migration()
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        role = connection.execute(sa.select(roles)).mappings().one()
        assert connection.execute(sa.select(role_events)).first() is None

    assert raced is True
    assert role["agent_paused_reason"] == "paused independently"
    assert role["version"] == 4
