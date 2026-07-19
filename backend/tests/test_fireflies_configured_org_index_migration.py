"""Contracts for revision 190's concurrent partial indexes."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.anthropic_batch_job import (
    ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_INDEX,
    ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_PREDICATE,
    AnthropicBatchJob,
)
from app.models.organization import (
    FIREFLIES_WEBHOOK_CONFIGURED_PREDICATE,
    Organization,
)


_INDEX_NAME = "ix_organizations_fireflies_webhook_configured"
_PREDICATE = "fireflies_webhook_secret IS NOT NULL AND fireflies_webhook_secret <> ''"
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "190_add_fireflies_configured_org_index.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "fireflies_configured_org_index_190",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_organization_metadata_retains_the_partial_index() -> None:
    migration = _migration()
    indexes = {index.name: index for index in Organization.__table__.indexes}
    configured_index = indexes[_INDEX_NAME]

    assert FIREFLIES_WEBHOOK_CONFIGURED_PREDICATE == _PREDICATE
    assert migration._FIREFLIES_INDEX.predicate == _PREDICATE
    assert [column.name for column in configured_index.columns] == ["id"]
    assert str(configured_index.dialect_options["postgresql"]["where"]) == _PREDICATE
    assert str(configured_index.dialect_options["sqlite"]["where"]) == _PREDICATE


def test_anthropic_batch_partial_index_is_migration_managed() -> None:
    migration = _migration()
    indexes = {index.name: index for index in AnthropicBatchJob.__table__.indexes}

    assert migration._ANTHROPIC_RECOVERY_INDEX.predicate == (
        ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_PREDICATE
    )
    assert ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_INDEX not in indexes


def test_revision_190_is_restart_safe_on_sqlite() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "organizations",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fireflies_webhook_secret", sa.String, nullable=True),
    )
    with engine.connect() as connection:
        metadata.create_all(connection)
        migration = _migration()
        assert migration.down_revision == "189_shared_family_reject_repair"
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()
        migration.upgrade()

        indexes = {
            index["name"]: index
            for index in sa.inspect(connection).get_indexes("organizations")
        }
        assert set(indexes) == {_INDEX_NAME}
        assert (
            str(indexes[_INDEX_NAME]["dialect_options"]["sqlite_where"]) == _PREDICATE
        )

        migration.downgrade()
        migration.downgrade()
        assert sa.inspect(connection).get_indexes("organizations") == []
    engine.dispose()


class _FakeResult:
    def __init__(self, state: dict[str, Any] | None):
        self._state = state

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._state


class _FakePostgresOperations:
    def __init__(
        self,
        specs: tuple[Any, ...],
        *,
        states: dict[str, dict[str, Any] | None],
    ):
        self.dialect = SimpleNamespace(name="postgresql")
        self.context = SimpleNamespace(as_sql=False)
        self.context.autocommit_block = self._autocommit_block
        self.executed: list[str] = []
        self._specs = {spec.name: spec for spec in specs}
        self._states = states

    @staticmethod
    def exact_state(spec: Any) -> dict[str, Any]:
        return {
            "indisvalid": True,
            "indisready": True,
            "indislive": True,
            "indisunique": False,
            "indisprimary": False,
            "indisexclusion": False,
            "indnkeyatts": len(spec.key_expressions),
            "indnatts": len(spec.key_expressions),
            "table_name": spec.table_name,
            "table_schema": "public",
            "current_schema": "public",
            "access_method": "btree",
            "key_expressions": list(spec.key_expressions),
            "predicate": spec.catalog_predicate,
        }

    @contextmanager
    def _autocommit_block(self):
        yield

    def get_context(self) -> Any:
        return self.context

    def get_bind(self) -> _FakePostgresOperations:
        return self

    def execute(
        self,
        statement: Any,
        parameters: dict[str, str] | None = None,
    ) -> _FakeResult | None:
        sql = str(statement)
        if "FROM pg_class AS index_class" in sql:
            assert parameters is not None
            return _FakeResult(self._states[str(parameters["index_name"])])
        self.executed.append(sql)
        for name, spec in self._specs.items():
            if sql.startswith("DROP INDEX") and sql.endswith(name):
                self._states[name] = None
            elif sql.startswith("CREATE INDEX") and f" {name} " in sql:
                self._states[name] = self.exact_state(spec)
        return None


@pytest.mark.parametrize(
    ("state", "expected_ddl_per_index"),
    [
        (None, ["CREATE INDEX CONCURRENTLY IF NOT EXISTS"]),
        (
            False,
            [
                "DROP INDEX CONCURRENTLY IF EXISTS",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
            ],
        ),
        (True, []),
    ],
)
def test_revision_190_repairs_absent_or_invalid_postgres_indexes(
    state: bool | None,
    expected_ddl_per_index: list[str],
) -> None:
    migration = _migration()
    states: dict[str, dict[str, Any] | None] = {}
    for spec in migration._POSTGRES_INDEXES:
        if state is None:
            states[spec.name] = None
        else:
            states[spec.name] = _FakePostgresOperations.exact_state(spec)
            if state is False:
                states[spec.name]["indisvalid"] = False
    operations = _FakePostgresOperations(
        migration._POSTGRES_INDEXES,
        states=states,
    )
    migration.op = operations

    migration.upgrade()

    expected_ddl = expected_ddl_per_index * len(migration._POSTGRES_INDEXES)
    assert len(operations.executed) == len(expected_ddl)
    for statement, expected in zip(operations.executed, expected_ddl, strict=True):
        assert statement.startswith(expected)


def test_revision_190_offline_sql_replaces_same_name_indexes() -> None:
    migration = _migration()
    operations = _FakePostgresOperations(
        migration._POSTGRES_INDEXES,
        states={spec.name: None for spec in migration._POSTGRES_INDEXES},
    )
    operations.context.as_sql = True
    migration.op = operations

    migration.upgrade()

    assert operations.executed == [
        statement
        for spec in migration._POSTGRES_INDEXES
        for statement in (
            f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}",
            migration._create_index_sql(spec, if_not_exists=False),
        )
    ]
    assert all(
        "IF NOT EXISTS" not in statement
        for statement in operations.executed
        if statement.startswith("CREATE INDEX")
    )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("table_name", "wrong_table"),
        ("table_schema", "wrong_schema"),
        ("indisunique", True),
        ("indisvalid", False),
        ("indisready", False),
        ("indislive", False),
        ("access_method", "hash"),
        ("key_expressions", ["wrong_column"]),
        ("predicate", "id > 0"),
    ],
)
@pytest.mark.parametrize("spec_position", [0, 1])
def test_revision_190_rebuilds_each_wrong_same_name_index_definition(
    field: str,
    wrong_value: Any,
    spec_position: int,
) -> None:
    migration = _migration()
    states = {
        spec.name: _FakePostgresOperations.exact_state(spec)
        for spec in migration._POSTGRES_INDEXES
    }
    target = migration._POSTGRES_INDEXES[spec_position]
    states[target.name][field] = wrong_value
    operations = _FakePostgresOperations(
        migration._POSTGRES_INDEXES,
        states=states,
    )
    migration.op = operations

    migration.upgrade()

    assert operations.executed == [
        f"DROP INDEX CONCURRENTLY IF EXISTS {target.name}",
        migration._create_index_sql(target),
    ]
    assert states[target.name] == _FakePostgresOperations.exact_state(target)


def test_revision_190_postgres_downgrade_is_concurrent_and_restart_safe() -> None:
    migration = _migration()
    operations = _FakePostgresOperations(
        migration._POSTGRES_INDEXES,
        states={
            spec.name: _FakePostgresOperations.exact_state(spec)
            for spec in migration._POSTGRES_INDEXES
        },
    )
    migration.op = operations

    migration.downgrade()

    assert operations.executed == [
        f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}"
        for spec in reversed(migration._POSTGRES_INDEXES)
    ]
