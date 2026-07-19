"""Data-repair contract for unsafe legacy shared-family auto-reject flags."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.scripts.database_migrate import (
    MigrationValidationError,
    _validate_shared_family_data_invariant,
    _validate_sqlite_shared_family_triggers,
)


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
                    "version": 8,
                    "auto_reject": True,
                    "auto_reject_pre_screen": False,
                    "role_kind": "sister",
                    "ats_owner_role_id": 2,
                    "deleted_at": None,
                },
                {
                    "id": 5,
                    "organization_id": 10,
                    "version": 1,
                    "auto_reject": False,
                    "auto_reject_pre_screen": False,
                    "role_kind": "sister",
                    "ats_owner_role_id": 4,
                    "deleted_at": None,
                },
                {
                    "id": 7,
                    "organization_id": 10,
                    "version": 2,
                    "auto_reject": True,
                    "auto_reject_pre_screen": True,
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
        assert by_id[3]["auto_reject"] is False
        assert by_id[3]["auto_reject_pre_screen"] is False
        assert by_id[3]["version"] == 9
        assert by_id[4]["version"] == 5
        assert by_id[6]["auto_reject"] is True
        assert by_id[7]["auto_reject"] is True
        assert by_id[7]["auto_reject_pre_screen"] is True
        assert by_id[7]["version"] == 2

        audits = {
            int(row["role_id"]): row
            for row in connection.execute(sa.select(events)).mappings()
        }
        assert set(audits) == {2, 3}
        owner_audit = audits[2]
        assert owner_audit["from_version"] == 4
        assert owner_audit["to_version"] == 5
        assert set(owner_audit["changes"]) == {
            "auto_reject",
            "auto_reject_pre_screen",
        }
        sister_audit = audits[3]
        assert sister_audit["from_version"] == 8
        assert sister_audit["to_version"] == 9
        assert set(sister_audit["changes"]) == {"auto_reject"}
        assert {
            row["request_id"] for row in audits.values()
        } == {"migration:189_shared_family_reject_repair"}


def test_sqlite_invariant_preserves_standalone_automation_and_blocks_shared_states():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, _events = _metadata()
    metadata.create_all(engine)
    migration = _migration()
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            triggers = set(
                connection.execute(
                    sa.text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND name LIKE "
                        "'enforce_shared_family_auto_reject_%_v189'"
                    )
                ).scalars()
            )
            assert triggers == {
                "enforce_shared_family_auto_reject_insert_v189",
                "enforce_shared_family_auto_reject_update_v189",
            }
            connection.execute(
                roles.insert(),
                [
                    {
                        "id": 1,
                        "organization_id": 10,
                        "version": 1,
                        "role_kind": "standard",
                        "auto_reject": True,
                        "auto_reject_pre_screen": True,
                    },
                    {
                        "id": 2,
                        "organization_id": 10,
                        "version": 1,
                        "role_kind": "standard",
                        "auto_reject": True,
                        "auto_reject_pre_screen": False,
                    },
                ],
            )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="shared role families cannot enable automatic rejection",
        ):
            with engine.begin() as connection:
                connection.execute(
                    roles.insert().values(
                        id=3,
                        organization_id=10,
                        version=1,
                        role_kind="sister",
                        ats_owner_role_id=2,
                        auto_reject=False,
                        auto_reject_pre_screen=False,
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                roles.update()
                .where(roles.c.id == 2)
                .values(auto_reject=False)
            )
            connection.execute(
                roles.insert().values(
                    id=3,
                    organization_id=10,
                    version=1,
                    role_kind="sister",
                    ats_owner_role_id=2,
                    auto_reject=False,
                    auto_reject_pre_screen=False,
                )
            )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="shared role families cannot enable automatic rejection",
        ):
            with engine.begin() as connection:
                connection.execute(
                    roles.update()
                    .where(roles.c.id == 2)
                    .values(auto_reject_pre_screen=True)
                )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="shared role families cannot enable automatic rejection",
        ):
            with engine.begin() as connection:
                connection.execute(
                    roles.update()
                    .where(roles.c.id == 3)
                    .values(ats_owner_role_id=1)
                )

        with engine.begin() as connection:
            connection.execute(
                roles.insert().values(
                    id=4,
                    organization_id=10,
                    version=1,
                    role_kind="sister",
                    ats_owner_role_id=2,
                    auto_reject=True,
                    auto_reject_pre_screen=True,
                    deleted_at=datetime.now(timezone.utc),
                )
            )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="shared role families cannot enable automatic rejection",
        ):
            with engine.begin() as connection:
                connection.execute(
                    roles.update()
                    .where(roles.c.id == 4)
                    .values(deleted_at=None)
                )

        with engine.begin() as connection:
            connection.execute(
                roles.update()
                .where(roles.c.id == 4)
                .values(
                    auto_reject=False,
                    auto_reject_pre_screen=False,
                    deleted_at=None,
                )
            )
            standalone = connection.execute(
                sa.select(
                    roles.c.auto_reject,
                    roles.c.auto_reject_pre_screen,
                ).where(roles.c.id == 1)
            ).one()
            assert standalone == (True, True)
    finally:
        engine.dispose()


def test_postgres_write_fence_sets_a_bounded_wait_before_locking_roles():
    migration = _migration()
    bind = Mock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.scalar_one.return_value = 0

    migration._fence_postgres_roles_writes(bind)

    assert bind.execute.call_count == 4
    current_timeout_statement = bind.execute.call_args_list[0].args[0]
    timeout_statement, timeout_parameters = bind.execute.call_args_list[1].args
    lock_statement = bind.execute.call_args_list[2].args[0]
    restore_statement, restore_parameters = bind.execute.call_args_list[3].args
    assert str(current_timeout_statement) == (
        "SELECT setting::bigint FROM pg_settings WHERE name = 'lock_timeout'"
    )
    assert str(timeout_statement) == (
        "SELECT set_config('lock_timeout', :timeout, true)"
    )
    assert timeout_parameters == {"timeout": "5000ms"}
    assert str(lock_statement) == (
        "LOCK TABLE roles IN EXCLUSIVE MODE"
    )
    assert str(restore_statement) == (
        "SELECT set_config('lock_timeout', :timeout, true)"
    )
    assert restore_parameters == {"timeout": "0ms"}


def test_postgres_shared_family_trigger_has_one_row_clause():
    migration = _migration()
    bind = Mock()
    bind.dialect.name = "postgresql"
    bind.dialect.identifier_preparer.quote.return_value = '"public"'
    bind.execute.return_value.scalar_one.return_value = "public"

    migration._install_postgres_shared_family_invariant(bind)

    trigger_ddl = str(bind.execute.call_args_list[-1].args[0])
    assert trigger_ddl.count("FOR EACH ROW") == 1
    assert "EXECUTE FUNCTION enforce_shared_family_auto_reject_v189()" in trigger_ddl


def test_postgres_write_fence_preserves_a_tighter_operator_timeout():
    migration = _migration()
    bind = Mock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.scalar_one.return_value = 200

    migration._fence_postgres_roles_writes(bind)

    assert bind.execute.call_count == 2
    assert "pg_settings" in str(bind.execute.call_args_list[0].args[0])
    assert str(bind.execute.call_args_list[1].args[0]) == (
        "LOCK TABLE roles IN EXCLUSIVE MODE"
    )


def test_postgres_write_fence_caps_a_looser_nonzero_operator_timeout():
    migration = _migration()
    bind = Mock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.scalar_one.return_value = 20_000

    migration._fence_postgres_roles_writes(bind)

    assert bind.execute.call_count == 4
    timeout_statement, timeout_parameters = bind.execute.call_args_list[1].args
    assert str(timeout_statement) == (
        "SELECT set_config('lock_timeout', :timeout, true)"
    )
    assert timeout_parameters == {"timeout": "5000ms"}
    assert str(bind.execute.call_args_list[2].args[0]) == (
        "LOCK TABLE roles IN EXCLUSIVE MODE"
    )
    restore_statement, restore_parameters = bind.execute.call_args_list[3].args
    assert str(restore_statement) == (
        "SELECT set_config('lock_timeout', :timeout, true)"
    )
    assert restore_parameters == {"timeout": "20000ms"}


def test_sqlite_write_fence_is_a_noop():
    migration = _migration()
    bind = Mock()
    bind.dialect.name = "sqlite"

    migration._fence_postgres_roles_writes(bind)

    bind.execute.assert_not_called()


def test_migration_refuses_cross_tenant_owner_edges_before_any_repair():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, events = _metadata()
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(
                roles.insert(),
                [
                    {
                        "id": 1,
                        "organization_id": 10,
                        "version": 4,
                        "auto_reject": True,
                        "auto_reject_pre_screen": True,
                        "role_kind": "standard",
                        "ats_owner_role_id": None,
                        "deleted_at": None,
                    },
                    {
                        "id": 2,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "sister",
                        "ats_owner_role_id": 1,
                        "deleted_at": None,
                    },
                    {
                        "id": 3,
                        "organization_id": 20,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "standard",
                        "ats_owner_role_id": None,
                        "deleted_at": None,
                    },
                    {
                        "id": 4,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "sister",
                        "ats_owner_role_id": 3,
                        # Deleted history remains invalid because restoring it
                        # would cross the tenant boundary.
                        "deleted_at": now,
                    },
                ],
            )

        migration = _migration()
        with pytest.raises(RuntimeError, match="cross-tenant ATS owner edge"):
            with engine.begin() as connection:
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()

        with engine.connect() as connection:
            owner = connection.execute(
                sa.select(
                    roles.c.auto_reject,
                    roles.c.auto_reject_pre_screen,
                    roles.c.version,
                ).where(roles.c.id == 1)
            ).one()
            assert owner == (True, True, 4)
            assert connection.execute(
                sa.select(sa.func.count()).select_from(events)
            ).scalar_one() == 0
            assert connection.execute(
                sa.text(
                    "SELECT count(*) FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE "
                    "'enforce_shared_family_auto_reject_%_v189'"
                )
            ).scalar_one() == 0
            with pytest.raises(
                MigrationValidationError,
                match="1 unsafe live role.*1 cross-tenant ATS owner edge",
            ):
                _validate_shared_family_data_invariant(connection)
    finally:
        engine.dispose()


def test_sqlite_trigger_and_data_validators_reject_drift_and_unsafe_restore():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, _events = _metadata()
    metadata.create_all(engine)
    migration = _migration()
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            _validate_sqlite_shared_family_triggers(connection)
            _validate_shared_family_data_invariant(connection)

            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_update_v189"
                )
            )
            with pytest.raises(MigrationValidationError, match="SQLite shared-family"):
                _validate_sqlite_shared_family_triggers(connection)

            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_insert_v189"
                )
            )
            migration._install_sqlite_shared_family_invariant(connection)
            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_insert_v189"
                )
            )
            connection.execute(
                sa.text(
                    "CREATE TRIGGER enforce_shared_family_auto_reject_insert_v189 "
                    "BEFORE INSERT ON roles BEGIN SELECT 1; END"
                )
            )
            with pytest.raises(MigrationValidationError, match="definition"):
                _validate_sqlite_shared_family_triggers(connection)

            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_insert_v189"
                )
            )
            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_update_v189"
                )
            )
            migration._install_sqlite_shared_family_invariant(connection)
            connection.execute(
                sa.text(
                    "DROP TRIGGER enforce_shared_family_auto_reject_insert_v189"
                )
            )
            connection.execute(
                roles.insert(),
                [
                    {
                        "id": 10,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": True,
                        "auto_reject_pre_screen": False,
                        "role_kind": "standard",
                        "ats_owner_role_id": None,
                    },
                    {
                        "id": 11,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "sister",
                        "ats_owner_role_id": 10,
                    },
                ],
            )
            with pytest.raises(MigrationValidationError, match="unsafe live role"):
                _validate_shared_family_data_invariant(connection)
    finally:
        engine.dispose()


@pytest.mark.parametrize("deleted", [False, True])
def test_sqlite_invariant_rejects_cross_tenant_edges_including_deleted_history(
    deleted: bool,
):
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, _events = _metadata()
    metadata.create_all(engine)
    migration = _migration()
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(
                roles.insert().values(
                    id=20,
                    organization_id=20,
                    version=1,
                    auto_reject=False,
                    auto_reject_pre_screen=False,
                    role_kind="standard",
                )
            )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="related roles cannot reference an ATS owner in another organization",
        ):
            with engine.begin() as connection:
                connection.execute(
                    roles.insert().values(
                        id=21,
                        organization_id=10,
                        version=1,
                        auto_reject=False,
                        auto_reject_pre_screen=False,
                        role_kind="sister",
                        ats_owner_role_id=20,
                        deleted_at=(datetime.now(timezone.utc) if deleted else None),
                    )
                )
    finally:
        engine.dispose()


def test_sqlite_invariant_rejects_both_tenant_mutation_directions():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata, roles, _events = _metadata()
    metadata.create_all(engine)
    migration = _migration()
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(
                roles.insert(),
                [
                    {
                        "id": 30,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "standard",
                        "ats_owner_role_id": None,
                    },
                    {
                        "id": 31,
                        "organization_id": 10,
                        "version": 1,
                        "auto_reject": False,
                        "auto_reject_pre_screen": False,
                        "role_kind": "sister",
                        "ats_owner_role_id": 30,
                    },
                ],
            )

        for role_id in (30, 31):
            with pytest.raises(
                sa.exc.IntegrityError,
                match=(
                    "related roles cannot reference an ATS owner in another "
                    "organization"
                ),
            ):
                with engine.begin() as connection:
                    connection.execute(
                        roles.update()
                        .where(roles.c.id == role_id)
                        .values(organization_id=20)
                    )
    finally:
        engine.dispose()


def test_migration_downgrade_refuses_to_restore_unsafe_automation():
    with pytest.raises(RuntimeError, match="cannot be restored safely"):
        _migration().downgrade()
