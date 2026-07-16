from __future__ import annotations

from unittest.mock import patch

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.scripts.database_migrate import (
    POSTGRES_ADVISORY_LOCK_ID,
    POSTGRES_REQUIRED_ASSESSMENT_STATUSES,
    POSTGRES_REQUIRED_INDEXES,
    POSTGRES_REQUIRED_TRIGGERS,
    MigrationSafetyError,
    _alembic_config,
    _preflight_database,
    main,
)
from tests.postgres_support import (
    configured_test_postgres_url,
    isolated_postgres_database,
    run_database_migrator as _run_migrator,
)


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(_alembic_config())


def test_migration_graph_has_canonical_initial_schema_and_one_head():
    script = _script_directory()

    assert script.get_bases() == ["000_initial_schema"]
    assert script.get_revision("000_initial_schema").down_revision is None
    assert script.get_revision("001").down_revision == "000_initial_schema"
    assert script.get_heads() == ["176_restore_application_timestamp_defaults"]


def test_preflight_allows_a_genuinely_empty_schema():
    engine = create_engine("sqlite://", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert _preflight_database(connection, _script_directory()) == "empty"
    finally:
        engine.dispose()


def test_preflight_rejects_unversioned_partial_schema_before_ddl():
    engine = create_engine("sqlite://", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.commit()

            with pytest.raises(MigrationSafetyError, match="unversioned non-empty"):
                _preflight_database(connection, _script_directory())

            assert inspect(connection).get_table_names() == ["users"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("revisions", [[], [""], ["001", "002"]])
def test_preflight_rejects_invalid_alembic_version_rows(revisions: list[str]):
    engine = create_engine("sqlite://", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(255))")
            )
            for revision in revisions:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": revision},
                )
            connection.commit()

            with pytest.raises(MigrationSafetyError, match="exactly one"):
                _preflight_database(connection, _script_directory())
    finally:
        engine.dispose()


def test_unexpected_migration_failure_is_sanitized(capsys):
    secret = "postgresql://admin:super-secret@example.invalid/production"

    with patch(
        "app.scripts.database_migrate.migrate_database",
        side_effect=RuntimeError(secret),
    ):
        assert main() == 1

    captured = capsys.readouterr()
    assert "migration failed (RuntimeError)" in captured.err
    assert secret not in captured.err


@pytest.fixture
def postgres_database_url() -> str:
    if not configured_test_postgres_url():
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL bootstrap tests")
    with isolated_postgres_database(prefix="bootstrap") as database_url:
        yield database_url


def test_fresh_postgres_schema_runs_full_chain_and_preserves_invariants(
    postgres_database_url: str,
):
    result = _run_migrator(postgres_database_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight passed (empty schema)" in result.stdout
    assert "schema invariant validation passed" in result.stdout

    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "176_restore_application_timestamp_defaults"

            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                ).scalars()
            )
            assert POSTGRES_REQUIRED_INDEXES <= indexes

            triggers = set(
                connection.execute(
                    text(
                        """
                        SELECT trigger.tgname
                        FROM pg_trigger AS trigger
                        JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE NOT trigger.tgisinternal
                          AND namespace.nspname = current_schema()
                        """
                    )
                ).scalars()
            )
            assert POSTGRES_REQUIRED_TRIGGERS <= triggers

            statuses = set(
                connection.execute(
                    text(
                        """
                        SELECT enum.enumlabel
                        FROM pg_type AS type
                        JOIN pg_enum AS enum ON enum.enumtypid = type.oid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = type.typnamespace
                        WHERE type.typname = 'assessmentstatus'
                          AND namespace.nspname = current_schema()
                        """
                    )
                ).scalars()
            )
            assert POSTGRES_REQUIRED_ASSESSMENT_STATUSES <= statuses

            version_length = connection.execute(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'alembic_version'
                      AND column_name = 'version_num'
                    """
                )
            ).scalar_one()
            assert version_length == 255
    finally:
        engine.dispose()


def test_partial_unversioned_postgres_schema_gets_zero_migration_ddl_and_unlocks(
    postgres_database_url: str,
):
    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))

        result = _run_migrator(postgres_database_url)

        assert result.returncode == 1
        assert "unversioned non-empty schema" in result.stderr
        assert "no migration DDL was applied" in result.stderr
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["users"]
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            ).scalar_one()
            assert acquired is True
            assert connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            ).scalar_one() is True
    finally:
        engine.dispose()


def test_postgres_migration_lock_timeout_is_bounded_and_applies_no_ddl(
    postgres_database_url: str,
):
    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.connect() as lock_holder:
            lock_holder.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            )
            lock_holder.commit()

            result = _run_migrator(
                postgres_database_url,
                lock_timeout_seconds=0.2,
            )

            assert result.returncode == 1
            assert "Timed out waiting for the database migration lock" in result.stderr
            with engine.connect() as observer:
                assert inspect(observer).get_table_names() == []

            assert lock_holder.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            ).scalar_one() is True
            lock_holder.commit()

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            ).scalar_one() is True
            assert connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
            ).scalar_one() is True
    finally:
        engine.dispose()
