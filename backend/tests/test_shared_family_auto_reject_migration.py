"""Data-repair contract for unsafe legacy shared-family auto-reject flags."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "189_repair_shared_family_auto_reject.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "shared_family_auto_reject_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata() -> tuple[sa.MetaData, sa.Table, sa.Table]:
    metadata = sa.MetaData()
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("version", sa.Integer, nullable=True),
        sa.Column("auto_reject", sa.Boolean, nullable=True),
        sa.Column("auto_reject_pre_screen", sa.Boolean, nullable=True),
        sa.Column("role_kind", sa.String, nullable=True),
        sa.Column("ats_owner_role_id", sa.Integer, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    events = sa.Table(
        "role_change_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer, nullable=True),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("from_version", sa.Integer, nullable=False),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("changes", sa.JSON, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("request_id", sa.String, nullable=True),
    )
    return metadata, roles, events


def test_migration_repairs_only_live_shared_families_and_records_the_fence():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, events = _metadata()
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            roles.insert(),
            [
                {
                    "id": 1,
                    "organization_id": 10,
                    "version": 3,
                    "auto_reject": True,
                    "auto_reject_pre_screen": False,
                },
                {
                    "id": 2,
                    "organization_id": 10,
                    "version": 4,
                    "auto_reject": True,
                    "auto_reject_pre_screen": True,
                },
                {
                    "id": 4,
                    "organization_id": 10,
                    "version": 5,
                    "auto_reject": False,
                    "auto_reject_pre_screen": False,
                },
                {
                    "id": 6,
                    "organization_id": 10,
                    "version": 6,
                    "auto_reject": True,
                    "auto_reject_pre_screen": True,
                },
            ],
        )
        connection.execute(
            roles.insert(),
            [
                {
                    "id": 3,
                    "organization_id": 10,
                    "role_kind": "sister",
                    "ats_owner_role_id": 2,
                    "deleted_at": None,
                },
                {
                    "id": 5,
                    "organization_id": 10,
                    "role_kind": "sister",
                    "ats_owner_role_id": 4,
                    "deleted_at": None,
                },
                {
                    "id": 7,
                    "organization_id": 10,
                    "role_kind": "sister",
                    "ats_owner_role_id": 6,
                    "deleted_at": datetime.now(timezone.utc),
                },
            ],
        )
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        by_id = {
            int(row.id): row
            for row in connection.execute(sa.select(roles)).mappings()
        }
        assert by_id[1]["auto_reject"] is True
        assert by_id[2]["auto_reject"] is False
        assert by_id[2]["auto_reject_pre_screen"] is False
        assert by_id[2]["version"] == 5
        assert by_id[4]["version"] == 5
        assert by_id[6]["auto_reject"] is True

        audit = connection.execute(sa.select(events)).mappings().one()
        assert audit["role_id"] == 2
        assert audit["from_version"] == 4
        assert audit["to_version"] == 5
        assert set(audit["changes"]) == {
            "auto_reject",
            "auto_reject_pre_screen",
        }
        assert audit["request_id"] == "migration:189_shared_family_reject_repair"


def test_migration_downgrade_refuses_to_restore_unsafe_automation():
    with pytest.raises(RuntimeError, match="cannot be restored safely"):
        _migration().downgrade()
