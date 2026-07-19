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
        / "176_add_graph_outbox_role_id.py"
    )
    spec = importlib.util.spec_from_file_location("graph_outbox_role_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payload_parser_rejects_pathological_legacy_json_without_raising():
    migration = _load_migration()

    assert migration._payload_mapping("9" * 5_000) == {}
    assert migration._payload_mapping("[" * 10_000 + "0" + "]" * 10_000) == {}


def test_upgrade_backfills_only_valid_same_org_pending_roles(monkeypatch):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    decisions = sa.Table(
        "agent_decisions",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
    )
    outbox = sa.Table(
        "graph_episode_outbox",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )
    sa.Index("ix_graph_episode_outbox_status", outbox.c.status)

    deleted_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata.create_all(connection)
        connection.execute(
            roles.insert(),
            [
                {"id": 10, "organization_id": 1, "deleted_at": None},
                {"id": 11, "organization_id": 1, "deleted_at": deleted_at},
                {"id": 20, "organization_id": 2, "deleted_at": None},
            ],
        )
        connection.execute(
            decisions.insert(),
            [
                {"id": 100, "organization_id": 1, "role_id": 10},
                {"id": 101, "organization_id": 2, "role_id": 20},
                {"id": 102, "organization_id": 1, "role_id": 11},
                {"id": 103, "organization_id": 1, "role_id": 20},
            ],
        )
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": 1,
                    "organization_id": 1,
                    "payload": {"role_id": 10},
                    "status": "pending",
                },
                {
                    "id": 2,
                    "organization_id": 1,
                    "payload": {"decision_id": 100},
                    "status": "pending",
                },
                {
                    "id": 3,
                    "organization_id": 1,
                    "payload": "{not-json",
                    "status": "pending",
                },
                {
                    "id": 4,
                    "organization_id": 1,
                    "payload": {"role_id": 20, "decision_id": 100},
                    "status": "pending",
                },
                {
                    "id": 5,
                    "organization_id": 1,
                    "payload": {"role_id": 11, "decision_id": 100},
                    "status": "pending",
                },
                {
                    "id": 6,
                    "organization_id": 1,
                    "payload": {"decision_id": 101},
                    "status": "pending",
                },
                {
                    "id": 7,
                    "organization_id": 1,
                    "payload": {"decision_id": 102},
                    "status": "pending",
                },
                {
                    "id": 8,
                    "organization_id": 1,
                    "payload": {"role_id": "10"},
                    "status": "pending",
                },
                {
                    "id": 9,
                    "organization_id": 1,
                    "payload": {"role_id": True, "decision_id": 100},
                    "status": "pending",
                },
                {
                    "id": 10,
                    "organization_id": 1,
                    "payload": {"role_id": 10},
                    "status": "sent",
                },
                {
                    "id": 11,
                    "organization_id": 1,
                    "payload": {"decision_id": 103},
                    "status": "pending",
                },
                {
                    "id": 12,
                    "organization_id": 1,
                    "payload": {"role_id": None, "decision_id": 100},
                    "status": "pending",
                },
                {
                    "id": 13,
                    "organization_id": 1,
                    "payload": {"decision_id": 1.5},
                    "status": "pending",
                },
                {
                    "id": 14,
                    "organization_id": 1,
                    "payload": None,
                    "status": "pending",
                },
                {
                    "id": 15,
                    "organization_id": 1,
                    "payload": {"role_id": "9" * 5_000},
                    "status": "pending",
                },
                {
                    "id": 16,
                    "organization_id": 1,
                    "payload": {"role_id": "2147483648"},
                    "status": "pending",
                },
                {
                    "id": 17,
                    "organization_id": 1,
                    "payload": {"decision_id": "9223372036854775808"},
                    "status": "pending",
                },
                {
                    "id": 18,
                    "organization_id": 1,
                    "payload": {"role_id": 10},
                    "status": "pending",
                },
                {
                    "id": 21,
                    "organization_id": 1,
                    "payload": {"role_id": 2_147_483_648},
                    "status": "pending",
                },
                {
                    "id": 22,
                    "organization_id": 1,
                    "payload": {"decision_id": 9_223_372_036_854_775_808},
                    "status": "pending",
                },
            ],
        )

        before = connection.execute(
            sa.select(outbox.c.id, outbox.c.payload, outbox.c.status).order_by(
                outbox.c.id
            )
        ).all()
        migration = _load_migration()
        assert migration.revision == "176_graph_outbox_role_id"
        assert migration.down_revision == "175_workspace_bulk_role_pause"
        monkeypatch.setattr(migration, "_BACKFILL_BATCH_SIZE", 2)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        migrated = sa.Table(
            "graph_episode_outbox", sa.MetaData(), autoload_with=connection
        )
        rows = connection.execute(
            sa.select(migrated.c.id, migrated.c.role_id).order_by(migrated.c.id)
        ).all()
        preserved = connection.execute(
            sa.select(migrated.c.id, migrated.c.payload, migrated.c.status).order_by(
                migrated.c.id
            )
        ).all()
        upgraded_inspector = sa.inspect(connection)
        indexes = upgraded_inspector.get_indexes("graph_episode_outbox")
        columns = upgraded_inspector.get_columns("graph_episode_outbox")
        foreign_keys = upgraded_inspector.get_foreign_keys("graph_episode_outbox")

        # The migration ships before application consumers. An old worker can
        # keep inserting without naming the new nullable column during rollout.
        connection.execute(
            migrated.insert(),
            {
                "id": 19,
                "organization_id": 1,
                "payload": {"role_id": 10},
                "status": "pending",
            },
        )
        assert connection.scalar(
            sa.select(migrated.c.role_id).where(migrated.c.id == 19)
        ) is None
        connection.execute(migrated.delete().where(migrated.c.id == 19))

        # Deleting a role must preserve the durable outbox record and clear
        # only its normalized ownership pointer.
        connection.execute(
            migrated.insert(),
            {
                "id": 20,
                "organization_id": 2,
                "payload": {"role_id": 20},
                "status": "pending",
                "role_id": 20,
            },
        )
        connection.execute(roles.delete().where(roles.c.id == 20))
        assert connection.scalar(
            sa.select(migrated.c.role_id).where(migrated.c.id == 20)
        ) is None
        connection.execute(migrated.delete().where(migrated.c.id == 20))

        migration.downgrade()
        downgraded = sa.Table(
            "graph_episode_outbox", sa.MetaData(), autoload_with=connection
        )
        downgraded_inspector = sa.inspect(connection)
        downgraded_columns = downgraded_inspector.get_columns(
            "graph_episode_outbox"
        )
        downgraded_indexes = downgraded_inspector.get_indexes(
            "graph_episode_outbox"
        )
        after_downgrade = connection.execute(
            sa.select(
                downgraded.c.id,
                downgraded.c.payload,
                downgraded.c.status,
            ).order_by(downgraded.c.id)
        ).all()

    assert rows == [
        (1, 10),
        (2, 10),
        (3, None),
        (4, None),
        (5, None),
        (6, None),
        (7, None),
        (8, 10),
        (9, None),
        (10, None),
        (11, None),
        (12, 10),
        (13, None),
        (14, None),
        (15, None),
        (16, None),
        (17, None),
        (18, 10),
        (21, None),
        (22, None),
    ]
    role_id_column = next(column for column in columns if column["name"] == "role_id")
    assert role_id_column["nullable"] is True
    assert preserved == before
    assert foreign_keys == [
        {
            "name": "fk_graph_episode_outbox_role_id_roles",
            "constrained_columns": ["role_id"],
            "referred_schema": None,
            "referred_table": "roles",
            "referred_columns": ["id"],
            "options": {"ondelete": "SET NULL"},
        }
    ]
    assert any(
        index["name"] == "ix_graph_episode_outbox_role_id"
        and index["column_names"] == ["role_id"]
        for index in indexes
    )
    assert "role_id" not in {column["name"] for column in downgraded_columns}
    assert {index["name"] for index in downgraded_indexes} == {
        "ix_graph_episode_outbox_status"
    }
    assert after_downgrade == before
