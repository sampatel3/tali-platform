from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
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
    MigrationValidationError,
    _alembic_config,
    _preflight_database,
    _require_supported_database_dialect,
    main,
    migrate_database,
)
from app.models.usage_event import UsageEvent
from app.platform.logging import JsonFormatter
from app.scripts.postgres_concurrent_indexes import (
    POSTGRES_CONCURRENT_INDEX_CONTRACTS,
    drifted_postgres_indexes,
)
from tests.postgres_support import (
    configured_test_postgres_url,
    isolated_postgres_database,
    run_alembic_upgrade,
    run_database_migrator as _run_migrator,
)


_LEGACY_SISTER_EVALUATION_COLUMNS = (
    "id",
    "organization_id",
    "role_id",
    "source_application_id",
    "status",
    "spec_fingerprint",
    "cv_fingerprint",
    "role_fit_score",
    "summary",
    "details",
    "history",
    "model_version",
    "prompt_version",
    "trace_id",
    "cache_hit",
    "error_message",
    "queued_at",
    "started_at",
    "scored_at",
    "created_at",
    "updated_at",
    "attempts",
    "next_attempt_at",
    "dispatch_attempted_at",
    "last_error_code",
)


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(_alembic_config())


def test_migration_graph_has_canonical_initial_schema_and_one_head():
    script = _script_directory()

    assert script.get_bases() == ["000_initial_schema"]
    assert script.get_revision("000_initial_schema").down_revision is None
    assert script.get_revision("001").down_revision == "000_initial_schema"
    assert script.get_revision("180_merge_related_role_workflow").down_revision == (
        "179_restore_schema_metadata_invariants",
        "174_related_role_workflow",
    )
    assert script.get_revision("181_merge_workspace_bulk_role_pause").down_revision == (
        "180_merge_related_role_workflow",
        "175_workspace_bulk_role_pause",
    )
    assert script.get_revision("182_workspace_pause_compat_audit").down_revision == (
        "181_merge_workspace_bulk_role_pause"
    )
    assert script.get_revision("183_preserve_related_role_history").down_revision == (
        "182_workspace_pause_compat_audit"
    )
    assert script.get_revision("184_assessment_result_delivery").down_revision == (
        "183_preserve_related_role_history"
    )
    assert script.get_revision("185_graph_ingest_dispatch").down_revision == (
        "184_assessment_result_delivery"
    )
    assert script.get_revision("186_graph_ingest_reconciliation").down_revision == (
        "185_graph_ingest_dispatch"
    )
    assert script.get_revision("187_graph_ingest_manifest").down_revision == (
        "186_graph_ingest_reconciliation"
    )
    assert script.get_revision("188_anthropic_batch_receipts").down_revision == (
        "187_graph_ingest_manifest"
    )
    assert script.get_revision("189_shared_family_reject_repair").down_revision == (
        "188_anthropic_batch_receipts"
    )
    assert script.get_revision("190_fireflies_org_index").down_revision == (
        "189_shared_family_reject_repair"
    )
    assert script.get_revision("191_task_repo_identity").down_revision == (
        "190_fireflies_org_index"
    )
    assert script.get_revision("192_scoring_batch_job_owner").down_revision == (
        "191_task_repo_identity"
    )
    assert script.get_revision("193_scoring_batch_indexes").down_revision == (
        "192_scoring_batch_job_owner"
    )
    assert script.get_revision("194_scoring_recovery_index").down_revision == (
        "193_scoring_batch_indexes"
    )
    assert script.get_revision(
        "195_compatibility_invariant_hardening"
    ).down_revision == "194_scoring_recovery_index"
    assert script.get_heads() == ["195_compatibility_invariant_hardening"]


def test_programmatic_alembic_config_resolves_scripts_from_any_cwd(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    script = ScriptDirectory.from_config(_alembic_config())

    assert script.get_heads() == ["195_compatibility_invariant_hardening"]


def test_direct_alembic_upgrade_resolves_imports_from_repo_root(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{tmp_path / 'root-cwd-upgrade.sqlite3'}"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(repository_root / "backend" / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_batch_reconciliation_index_is_retained_in_orm_metadata() -> None:
    indexes = {index.name: index for index in UsageEvent.__table__.indexes}
    batch_index = indexes["ix_usage_events_batch_id"]

    assert "metadata ->> 'batch_id'" in str(batch_index.expressions[0])
    assert str(batch_index.dialect_options["postgresql"]["where"]) == (
        "metadata IS NOT NULL"
    )


def test_preflight_allows_a_genuinely_empty_schema():
    engine = create_engine("sqlite://", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert _preflight_database(connection, _script_directory()) == "empty"
    finally:
        engine.dispose()


def test_fresh_sqlite_schema_runs_full_chain_and_reruns_safely(tmp_path):
    database = tmp_path / "migration-contract.sqlite3"
    database_url = f"sqlite:///{database}"

    assert migrate_database(database_url) == "empty"
    assert migrate_database(database_url) == "versioned"

    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
            indexes = {
                index["name"]: index
                for index in inspect(connection).get_indexes("organizations")
            }
            assert "ix_organizations_fireflies_webhook_configured" in indexes
            score_columns = {
                column["name"]: column
                for column in inspect(connection).get_columns("cv_score_jobs")
            }
            assert score_columns["batch_run_id"]["nullable"] is True
            score_indexes = {
                index["name"]: index
                for index in inspect(connection).get_indexes("cv_score_jobs")
            }
            assert {
                "ix_cv_score_jobs_batch_run_app_attempt",
                "uq_cv_score_jobs_batch_run_app_active",
            } <= set(score_indexes)
            assert score_indexes["uq_cv_score_jobs_batch_run_app_active"]["unique"]
            recovery_indexes = {
                index["name"]: index
                for index in inspect(connection).get_indexes("background_job_runs")
            }
            assert recovery_indexes["ix_background_job_runs_scoring_recovery_active"][
                "column_names"
            ] == ["scope_kind", "id"]
            batch_foreign_key = next(
                foreign_key
                for foreign_key in inspect(connection).get_foreign_keys("cv_score_jobs")
                if foreign_key["name"] == "fk_cv_score_jobs_batch_run_id"
            )
            assert batch_foreign_key["referred_table"] == "background_job_runs"
            assert batch_foreign_key["options"].get("ondelete") == "SET NULL"
            triggers = {
                str(name)
                for name in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND name LIKE "
                        "'enforce_shared_family_auto_reject_%_v189'"
                    )
                ).scalars()
            }
            assert triggers == {
                "enforce_shared_family_auto_reject_insert_v189",
                "enforce_shared_family_auto_reject_update_v189",
            }
    finally:
        engine.dispose()


def test_sqlite_migrator_rejects_missing_scoring_batch_index(tmp_path):
    database = tmp_path / "migration-scoring-index-drift.sqlite3"
    database_url = f"sqlite:///{database}"
    assert migrate_database(database_url) == "empty"
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX uq_cv_score_jobs_batch_run_app_active"))
        with pytest.raises(
            MigrationValidationError,
            match="scoring-batch index is missing or drifted",
        ):
            migrate_database(database_url)
    finally:
        engine.dispose()


def test_sqlite_migrator_rejects_drifted_scoring_recovery_index(tmp_path):
    database = tmp_path / "migration-scoring-recovery-index-drift.sqlite3"
    database_url = f"sqlite:///{database}"
    assert migrate_database(database_url) == "empty"
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DROP INDEX ix_background_job_runs_scoring_recovery_active")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_background_job_runs_scoring_recovery_active "
                    "ON background_job_runs (scope_kind, id) "
                    "WHERE kind = 'scoring_batch'"
                )
            )
        with pytest.raises(
            MigrationValidationError,
            match="scoring recovery index is missing or drifted",
        ):
            migrate_database(database_url)
    finally:
        engine.dispose()


def test_migration_keeps_existing_application_loggers_enabled(tmp_path):
    application_logger = logging.getLogger("taali.tests.migration-preservation")
    root_logger = logging.getLogger()
    original_root_handlers = tuple(root_logger.handlers)
    original_root_level = root_logger.level
    original_application_state = (
        application_logger.disabled,
        application_logger.level,
        application_logger.propagate,
        tuple(application_logger.handlers),
    )
    stream = io.StringIO()
    structured_handler = logging.StreamHandler(stream)
    structured_handler.setFormatter(JsonFormatter())
    root_logger.handlers[:] = [structured_handler]
    root_logger.setLevel(logging.INFO)
    application_logger.disabled = False
    application_logger.setLevel(logging.NOTSET)
    application_logger.propagate = True
    application_logger.handlers.clear()
    try:
        database_url = f"sqlite:///{tmp_path / 'logging-contract.sqlite3'}"
        assert migrate_database(database_url) == "empty"
        assert tuple(root_logger.handlers) == (structured_handler,)
        assert root_logger.level == logging.INFO
        assert application_logger.disabled is False
        application_logger.info("post-migration-info-evidence")
        application_logger.error(
            "post-migration failure error=%s",
            RuntimeError("migration-secret-sentinel"),
        )
        encoded = stream.getvalue()
        assert "post-migration-info-evidence" in encoded
        assert "migration-secret-sentinel" not in encoded
        assert "RuntimeError" in encoded
    finally:
        root_logger.handlers[:] = original_root_handlers
        root_logger.setLevel(original_root_level)
        (
            application_logger.disabled,
            application_logger.level,
            application_logger.propagate,
            original_application_handlers,
        ) = original_application_state
        application_logger.handlers[:] = original_application_handlers
        structured_handler.close()


def test_migrator_subprocess_emits_structured_revision_progress(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'logging-wrapper.sqlite3'}"

    result = _run_migrator(database_url)

    assert result.returncode == 0, result.stdout + result.stderr
    payloads = []
    for line in result.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    assert any(
        payload.get("logger") == "alembic.runtime.migration"
        and str(payload.get("message")).startswith(
            "Running migration operation=upgrade from="
        )
        and "to=000_initial_schema" in str(payload.get("message"))
        for payload in payloads
    )
    assert "INFO  [" not in result.stderr


def test_migrator_rejects_unsupported_dialect_before_preflight_or_ddl():
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="unsupported-test-dialect")
    )

    with pytest.raises(MigrationSafetyError, match="unsupported database dialect"):
        _require_supported_database_dialect(connection)


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
                    text(
                        "INSERT INTO alembic_version (version_num) VALUES (:revision)"
                    ),
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
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )

            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                ).scalars()
            )
            assert POSTGRES_REQUIRED_INDEXES <= indexes
            assert drifted_postgres_indexes(connection) == ()
            score_columns = {
                column["name"]: column
                for column in inspect(connection).get_columns("cv_score_jobs")
            }
            assert score_columns["batch_run_id"]["nullable"] is True
            score_foreign_keys = {
                foreign_key["name"]: foreign_key
                for foreign_key in inspect(connection).get_foreign_keys("cv_score_jobs")
            }
            batch_foreign_key = score_foreign_keys["fk_cv_score_jobs_batch_run_id"]
            assert batch_foreign_key["constrained_columns"] == ["batch_run_id"]
            assert batch_foreign_key["referred_table"] == "background_job_runs"
            assert batch_foreign_key["referred_columns"] == ["id"]
            assert batch_foreign_key["options"]["ondelete"] == "SET NULL"
            assert (
                connection.execute(
                    text(
                        "SELECT convalidated FROM pg_constraint "
                        "WHERE conname = 'fk_cv_score_jobs_batch_run_id'"
                    )
                ).scalar_one()
                is True
            )
            assert "ix_assessments_workable_result_delivery_recovery" in indexes
            assert "ix_graph_ingest_dispatches_reconciliation" in indexes
            assessment_columns = {
                column["name"]
                for column in inspect(connection).get_columns("assessments")
            }
            assert {
                "workable_result_delivery_status",
                "workable_result_delivery_receipt",
                "workable_result_delivery_next_attempt_at",
                "workable_result_delivery_claimed_at",
            } <= assessment_columns
            graph_columns = {
                column["name"]
                for column in inspect(connection).get_columns("graph_ingest_dispatches")
            }
            assert {
                "reconciliation_history",
                "operation_manifest",
                "operation_manifest_sha256",
            } <= graph_columns

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
            assert "trg_graph_ingest_manifest_immutable" in triggers
            assert "trg_anthropic_batch_receipt_immutable" in triggers

            manifest_constraint = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(constraint_row.oid) "
                    "FROM pg_constraint AS constraint_row "
                    "WHERE constraint_row.conname = "
                    "'ck_graph_ingest_dispatches_manifest_pair'"
                )
            ).scalar_one()
            assert "operation_manifest IS NULL" in manifest_constraint
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_proc WHERE proname = "
                        "'prevent_graph_ingest_manifest_mutation_v187'"
                    )
                ).scalar_one()
                == 1
            )

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
            assert (
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                ).scalar_one()
                is True
            )
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

            assert (
                lock_holder.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                ).scalar_one()
                is True
            )
            lock_holder.commit()

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                ).scalar_one()
                is True
            )
            assert (
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()


def test_scoring_batch_ownership_upgrades_from_revision_191(
    postgres_database_url: str,
) -> None:
    historical = run_alembic_upgrade(
        postgres_database_url,
        revision="191_task_repo_identity",
    )
    assert historical.returncode == 0, historical.stdout + historical.stderr
    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert "batch_run_id" not in {
                column["name"]
                for column in inspect(connection).get_columns("cv_score_jobs")
            }

        result = _run_migrator(postgres_database_url)
        assert result.returncode == 0, result.stdout + result.stderr

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
            assert drifted_postgres_indexes(connection) == ()
            foreign_keys = {
                foreign_key["name"]: foreign_key
                for foreign_key in inspect(connection).get_foreign_keys("cv_score_jobs")
            }
            assert (
                foreign_keys["fk_cv_score_jobs_batch_run_id"]["options"]["ondelete"]
                == "SET NULL"
            )
    finally:
        engine.dispose()


def test_scoring_recovery_index_upgrades_from_revision_193_and_repairs_drift(
    postgres_database_url: str,
) -> None:
    historical = run_alembic_upgrade(
        postgres_database_url,
        revision="193_scoring_batch_indexes",
    )
    assert historical.returncode == 0, historical.stdout + historical.stderr
    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "193_scoring_batch_indexes"
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_background_job_runs_scoring_recovery_active "
                    "ON background_job_runs (status)"
                )
            )

        result = _run_migrator(postgres_database_url)
        assert result.returncode == 0, result.stdout + result.stderr

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
            assert drifted_postgres_indexes(connection) == ()
    finally:
        engine.dispose()


def test_versioned_postgres_ddl_timeout_rolls_back_compatibility_revisions(
    postgres_database_url: str,
):
    historical = run_alembic_upgrade(
        postgres_database_url,
        revision="181_merge_workspace_bulk_role_pause",
    )
    assert historical.returncode == 0, historical.stdout + historical.stderr

    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.connect() as lock_holder:
            lock_holder.execute(text("LOCK TABLE roles IN ACCESS EXCLUSIVE MODE"))

            result = _run_migrator(
                postgres_database_url,
                lock_timeout_seconds=0.2,
            )
            assert result.returncode == 1
            assert "migration failed" in result.stderr

            with engine.connect() as observer:
                assert (
                    observer.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "181_merge_workspace_bulk_role_pause"
                )
                assert not inspect(observer).has_table(
                    "workspace_pause_migration_audits"
                )
                action_check = observer.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(constraint_row.oid)
                        FROM pg_constraint AS constraint_row
                        WHERE constraint_row.conname =
                              'ck_workspace_agent_control_events_action'
                        """
                    )
                ).scalar_one()
                assert "migrated" not in action_check

            lock_holder.rollback()

        recovered = _run_migrator(postgres_database_url)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
    finally:
        engine.dispose()


def test_revision_190_rebuilds_valid_same_name_wrong_postgres_indexes(
    postgres_database_url: str,
) -> None:
    historical = run_alembic_upgrade(
        postgres_database_url,
        revision="189_shared_family_reject_repair",
    )
    assert historical.returncode == 0, historical.stdout + historical.stderr

    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_organizations_fireflies_webhook_configured "
                    "ON organizations (name)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX "
                    "ix_anthropic_batch_jobs_known_accepted_recovery "
                    "ON anthropic_batch_jobs (batch_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_background_job_runs_scoring_recovery_active "
                    "ON background_job_runs (status)"
                )
            )

        result = _run_migrator(postgres_database_url)
        assert result.returncode == 0, result.stdout + result.stderr

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT
                        index_class.relname AS index_name,
                        table_class.relname AS table_name,
                        index_state.indisvalid,
                        index_state.indisready,
                        index_state.indislive,
                        index_state.indisunique,
                        index_state.indnkeyatts,
                        index_state.indnatts,
                        ARRAY(
                            SELECT pg_get_indexdef(
                                index_state.indexrelid,
                                key_position,
                                true
                            )
                            FROM generate_series(
                                1,
                                index_state.indnkeyatts
                            ) AS position(key_position)
                            ORDER BY key_position
                        ) AS key_expressions,
                        pg_get_expr(
                            index_state.indpred,
                            index_state.indrelid,
                            true
                        ) AS predicate
                    FROM pg_class AS index_class
                    JOIN pg_index AS index_state
                      ON index_state.indexrelid = index_class.oid
                    JOIN pg_class AS table_class
                      ON table_class.oid = index_state.indrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = index_class.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND index_class.relname IN (
                          'ix_organizations_fireflies_webhook_configured',
                          'ix_anthropic_batch_jobs_known_accepted_recovery',
                          'ix_cv_score_jobs_batch_run_app_attempt',
                          'uq_cv_score_jobs_batch_run_app_active',
                          'ix_background_job_runs_scoring_recovery_active'
                      )
                    """
                    )
                )
                .mappings()
                .all()
            )
            by_name = {str(row["index_name"]): row for row in rows}
            assert set(by_name) == set(POSTGRES_CONCURRENT_INDEX_CONTRACTS)
            for index_name, contract in POSTGRES_CONCURRENT_INDEX_CONTRACTS.items():
                state = by_name[index_name]
                assert state["table_name"] == contract.table_name
                assert state["indisvalid"] is True
                assert state["indisready"] is True
                assert state["indislive"] is True
                assert bool(state["indisunique"]) is contract.unique
                assert state["indnkeyatts"] == len(contract.key_expressions)
                assert state["indnatts"] == len(contract.key_expressions)
                assert tuple(state["key_expressions"]) == contract.key_expressions
                assert " ".join(str(state["predicate"]).split()) == " ".join(
                    contract.catalog_predicate.split()
                )
    finally:
        engine.dispose()


def test_revision_190_concurrent_build_obeys_timeout_and_releases_advisory_lock(
    postgres_database_url: str,
) -> None:
    historical = run_alembic_upgrade(
        postgres_database_url,
        revision="189_shared_family_reject_repair",
    )
    assert historical.returncode == 0, historical.stdout + historical.stderr

    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.connect() as blocker:
            blocker.execute(text("LOCK TABLE organizations IN ACCESS EXCLUSIVE MODE"))
            started_at = time.monotonic()
            try:
                result = _run_migrator(
                    postgres_database_url,
                    lock_timeout_seconds=0.2,
                    process_timeout_seconds=5.0,
                )
                elapsed = time.monotonic() - started_at
                assert elapsed < 4.0
                assert result.returncode == 1
                assert "migration failed" in result.stderr

                with engine.connect() as observer:
                    assert (
                        observer.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalar_one()
                        == "189_shared_family_reject_repair"
                    )
                    assert (
                        observer.execute(
                            text("SELECT pg_try_advisory_lock(:lock_id)"),
                            {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                        ).scalar_one()
                        is True
                    )
                    assert (
                        observer.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                        ).scalar_one()
                        is True
                    )
            finally:
                blocker.rollback()

        recovered = _run_migrator(postgres_database_url)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
    finally:
        engine.dispose()


def test_postgres_upgrade_from_published_related_role_head_preserves_data_and_pause_state(
    postgres_database_url: str,
):
    branch_result = run_alembic_upgrade(
        postgres_database_url,
        revision="173_related_role_drafts",
    )
    assert branch_result.returncode == 0, branch_result.stdout + branch_result.stderr

    paused_at = datetime(2026, 7, 16, 8, 9, 10, tzinfo=timezone.utc)
    pipeline_updated_at = datetime(2026, 7, 15, 11, 12, 13, tzinfo=timezone.utc)
    queued_at = datetime(2026, 7, 15, 11, 13, 0, tzinfo=timezone.utc)
    started_at = datetime(2026, 7, 15, 11, 14, 0, tzinfo=timezone.utc)
    scored_at = datetime(2026, 7, 15, 11, 15, 0, tzinfo=timezone.utc)
    created_at = datetime(2026, 7, 15, 11, 12, 30, tzinfo=timezone.utc)
    updated_at = datetime(2026, 7, 15, 11, 16, 0, tzinfo=timezone.utc)
    dispatch_attempted_at = datetime(
        2026,
        7,
        15,
        11,
        13,
        30,
        tzinfo=timezone.utc,
    )
    pause_reason = "emergency hold: recruiter requested"
    evaluation_columns = ", ".join(_LEGACY_SISTER_EVALUATION_COLUMNS)

    engine = create_engine(postgres_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "173_related_role_drafts"
            )

            inspector = inspect(connection)
            pre_upgrade_columns = {
                column["name"]
                for column in inspector.get_columns("sister_role_evaluations")
            }
            assert {
                "pipeline_stage",
                "pipeline_stage_updated_at",
                "pipeline_stage_source",
            }.isdisjoint(pre_upgrade_columns)
            assert "ix_sister_evaluations_role_pipeline_stage" not in {
                index["name"]
                for index in inspector.get_indexes("sister_role_evaluations")
            }

            connection.execute(
                text(
                    """
                    INSERT INTO organizations (
                        id,
                        name,
                        sso_enforced,
                        saml_enabled,
                        billing_provider,
                        credits_balance,
                        default_assessment_duration_minutes,
                        fireflies_single_account_mode,
                        two_factor_required,
                        sync_mode,
                        bullhorn_credential_generation,
                        agent_workspace_paused_at,
                        agent_workspace_paused_reason,
                        agent_workspace_paused_by_name,
                        agent_workspace_control_version
                    ) VALUES (
                        1001,
                        'Migration preservation workspace',
                        false,
                        false,
                        'lemon',
                        0,
                        30,
                        true,
                        false,
                        'standalone',
                        0,
                        :paused_at,
                        :pause_reason,
                        'Original Recruiter',
                        7
                    )
                    """
                ),
                {"paused_at": paused_at, "pause_reason": pause_reason},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id,
                        email,
                        hashed_password,
                        is_active,
                        is_superuser,
                        is_verified,
                        organization_id,
                        role,
                        failed_login_attempts
                    ) VALUES (
                        1101,
                        'migration-owner@example.test',
                        'not-a-real-password-hash',
                        true,
                        false,
                        true,
                        1001,
                        'owner',
                        0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE organizations
                    SET agent_workspace_paused_by_user_id = 1101
                    WHERE id = 1001
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO roles (
                        id,
                        organization_id,
                        name,
                        source,
                        reject_threshold,
                        starred_for_auto_sync,
                        agentic_mode_enabled,
                        auto_reject,
                        auto_promote,
                        auto_reject_threshold_mode,
                        star_auto_managed,
                        auto_skip_assessment,
                        auto_reject_pre_screen,
                        role_kind,
                        ats_owner_role_id,
                        version
                    ) VALUES (
                        :id,
                        1001,
                        :name,
                        'manual',
                        60,
                        false,
                        :agentic_mode_enabled,
                        false,
                        false,
                        'auto',
                        false,
                        false,
                        false,
                        :role_kind,
                        :ats_owner_role_id,
                        :version
                    )
                    """
                ),
                [
                    {
                        "id": 2001,
                        "name": "Platform Engineer",
                        "agentic_mode_enabled": True,
                        "role_kind": "standard",
                        "ats_owner_role_id": None,
                        "version": 11,
                    },
                    {
                        "id": 2002,
                        "name": "Related Systems Engineer",
                        "agentic_mode_enabled": False,
                        "role_kind": "sister",
                        "ats_owner_role_id": 2001,
                        "version": 4,
                    },
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO candidates (
                        id,
                        organization_id,
                        email,
                        full_name,
                        marketing_consent,
                        workable_enriched
                    ) VALUES (
                        3001,
                        1001,
                        'preserved-candidate@example.test',
                        'Preserved Candidate',
                        true,
                        false
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO candidate_applications (
                        id,
                        organization_id,
                        candidate_id,
                        role_id,
                        status,
                        source,
                        pipeline_stage,
                        pipeline_stage_updated_at,
                        pipeline_stage_source,
                        application_outcome,
                        application_outcome_updated_at,
                        version
                    ) VALUES (
                        4001,
                        1001,
                        3001,
                        2001,
                        'applied',
                        'manual',
                        'applied',
                        :pipeline_updated_at,
                        'manual',
                        'open',
                        :pipeline_updated_at,
                        5
                    )
                    """
                ),
                {"pipeline_updated_at": pipeline_updated_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sister_role_evaluations (
                        id,
                        organization_id,
                        role_id,
                        source_application_id,
                        status,
                        spec_fingerprint,
                        cv_fingerprint,
                        role_fit_score,
                        summary,
                        details,
                        history,
                        model_version,
                        prompt_version,
                        trace_id,
                        cache_hit,
                        error_message,
                        queued_at,
                        started_at,
                        scored_at,
                        created_at,
                        updated_at,
                        attempts,
                        next_attempt_at,
                        dispatch_attempted_at,
                        last_error_code
                    ) VALUES (
                        5001,
                        1001,
                        2002,
                        4001,
                        'completed',
                        :spec_fingerprint,
                        :cv_fingerprint,
                        92.5,
                        'Strong systems fit; retain exactly.',
                        CAST(:details AS JSON),
                        CAST(:history AS JSON),
                        'migration-test-model',
                        'migration-test-prompt-v3',
                        'migration-test-trace',
                        true,
                        NULL,
                        :queued_at,
                        :started_at,
                        :scored_at,
                        :created_at,
                        :updated_at,
                        3,
                        NULL,
                        :dispatch_attempted_at,
                        NULL
                    )
                    """
                ),
                {
                    "spec_fingerprint": "a" * 64,
                    "cv_fingerprint": "b" * 64,
                    "details": json.dumps(
                        {
                            "strengths": ["Python", "distributed systems"],
                            "explanation": "This payload must survive unchanged.",
                        }
                    ),
                    "history": json.dumps(
                        [
                            {"status": "pending", "sequence": 1},
                            {"status": "completed", "sequence": 2},
                        ]
                    ),
                    "queued_at": queued_at,
                    "started_at": started_at,
                    "scored_at": scored_at,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "dispatch_attempted_at": dispatch_attempted_at,
                },
            )
            legacy_evaluation = dict(
                connection.execute(
                    text(
                        f"SELECT {evaluation_columns} "
                        "FROM sister_role_evaluations WHERE id = 5001"
                    )
                )
                .mappings()
                .one()
            )

        # Revision 180 was exercised before the workspace conversion existed.
        # Pin that published state explicitly: changing its parent would make a
        # database already stamped at 180 silently skip revision 175.
        published_head_result = run_alembic_upgrade(
            postgres_database_url,
            revision="180_merge_related_role_workflow",
        )
        assert published_head_result.returncode == 0, (
            published_head_result.stdout + published_head_result.stderr
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "180_merge_related_role_workflow"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT agent_workspace_paused_at FROM organizations "
                        "WHERE id = 1001"
                    )
                ).scalar_one()
                == paused_at
            )
            assert (
                connection.execute(
                    text("SELECT agent_paused_at FROM roles WHERE id = 2001")
                ).scalar_one()
                is None
            )
            assert (
                dict(
                    connection.execute(
                        text(
                            f"SELECT {evaluation_columns} "
                            "FROM sister_role_evaluations WHERE id = 5001"
                        )
                    )
                    .mappings()
                    .one()
                )
                == legacy_evaluation
            )

        migration_result = _run_migrator(postgres_database_url)
        assert migration_result.returncode == 0, (
            migration_result.stdout + migration_result.stderr
        )
        assert "Preflight passed (versioned schema)" in migration_result.stdout
        assert (
            "Migration and schema invariant validation passed."
            in migration_result.stdout
        )

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )

            preserved_evaluation = dict(
                connection.execute(
                    text(
                        f"SELECT {evaluation_columns} "
                        "FROM sister_role_evaluations WHERE id = 5001"
                    )
                )
                .mappings()
                .one()
            )
            assert preserved_evaluation == legacy_evaluation

            workflow_state = (
                connection.execute(
                    text(
                        """
                    SELECT
                        pipeline_stage,
                        pipeline_stage_updated_at,
                        pipeline_stage_source
                    FROM sister_role_evaluations
                    WHERE id = 5001
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert workflow_state["pipeline_stage"] == "applied"
            assert workflow_state["pipeline_stage_updated_at"] is not None
            assert workflow_state["pipeline_stage_source"] == "system"

            related_indexes = {
                index["name"]: index
                for index in inspect(connection).get_indexes("sister_role_evaluations")
            }
            workflow_index = related_indexes[
                "ix_sister_evaluations_role_pipeline_stage"
            ]
            assert workflow_index["column_names"] == [
                "role_id",
                "pipeline_stage",
            ]
            assert workflow_index["unique"] is False

            organization = (
                connection.execute(
                    text(
                        """
                    SELECT
                        agent_workspace_paused_at,
                        agent_workspace_paused_reason,
                        agent_workspace_paused_by_user_id,
                        agent_workspace_paused_by_name,
                        agent_workspace_control_version
                    FROM organizations
                    WHERE id = 1001
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert organization == {
                "agent_workspace_paused_at": None,
                "agent_workspace_paused_reason": None,
                "agent_workspace_paused_by_user_id": None,
                "agent_workspace_paused_by_name": None,
                "agent_workspace_control_version": 8,
            }

            owner_role = (
                connection.execute(
                    text(
                        """
                    SELECT agent_paused_at, agent_paused_reason, version
                    FROM roles
                    WHERE id = 2001
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert owner_role == {
                "agent_paused_at": paused_at,
                "agent_paused_reason": "paused by workspace control",
                "version": 12,
            }
            related_role = (
                connection.execute(
                    text(
                        """
                    SELECT agent_paused_at, agent_paused_reason, version
                    FROM roles
                    WHERE id = 2002
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert related_role == {
                "agent_paused_at": None,
                "agent_paused_reason": None,
                "version": 4,
            }

            role_event = (
                connection.execute(
                    text(
                        """
                    SELECT
                        id,
                        organization_id,
                        role_id,
                        actor_user_id,
                        action,
                        from_version,
                        to_version,
                        changes,
                        reason,
                        request_id
                    FROM role_change_events
                    WHERE role_id = 2001 AND action = 'agent_paused'
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert role_event["organization_id"] == 1001
            assert role_event["actor_user_id"] == 1101
            assert role_event["from_version"] == 11
            assert role_event["to_version"] == 12
            assert role_event["request_id"] is None
            assert role_event["changes"] == {
                "agent_paused_at": {
                    "before": None,
                    "after": paused_at.isoformat(),
                },
                "agent_paused_reason": {
                    "before": None,
                    "after": "paused by workspace control",
                },
            }
            assert role_event["reason"] == (
                "workspace pause migrated to role bulk control"
            )

            workspace_event = (
                connection.execute(
                    text(
                        """
                    SELECT
                        actor_user_id,
                        actor_name,
                        action,
                        from_version,
                        to_version,
                        reason,
                        request_id
                    FROM workspace_agent_control_events
                    WHERE organization_id = 1001
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert workspace_event["actor_user_id"] is None
            assert workspace_event["actor_name"] == "Taali migration"
            assert workspace_event["action"] == "migrated"
            assert workspace_event["from_version"] == 7
            assert workspace_event["to_version"] == 8
            assert "no role was resumed" in workspace_event["reason"]
            assert workspace_event["request_id"] == (
                "migration:182_workspace_pause_compat_audit:1001"
            )

            compatibility_audit = (
                connection.execute(
                    text(
                        """
                    SELECT
                        evidence_source,
                        evidence_quality,
                        converted_role_count,
                        source_role_event_ids,
                        source_role_ids,
                        compatibility_applied,
                        control_version_before,
                        control_version_after,
                        anomalies
                    FROM workspace_pause_migration_audits
                    WHERE organization_id = 1001
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert compatibility_audit["evidence_source"] == (
                "published_175_role_events"
            )
            assert compatibility_audit["evidence_quality"] == "exact"
            assert compatibility_audit["converted_role_count"] == 1
            assert compatibility_audit["source_role_event_ids"] == [role_event["id"]]
            assert compatibility_audit["source_role_ids"] == [2001]
            assert compatibility_audit["compatibility_applied"] is True
            assert compatibility_audit["control_version_before"] == 7
            assert compatibility_audit["control_version_after"] == 8
            assert compatibility_audit["anomalies"] == []

            role_owner_fk = next(
                fk
                for fk in inspect(connection).get_foreign_keys("roles")
                if fk["constrained_columns"] == ["ats_owner_role_id"]
            )
            evaluation_role_fk = next(
                fk
                for fk in inspect(connection).get_foreign_keys(
                    "sister_role_evaluations"
                )
                if fk["constrained_columns"] == ["role_id"]
            )
            assert role_owner_fk["options"]["ondelete"] == "RESTRICT"
            assert evaluation_role_fk["options"]["ondelete"] == "RESTRICT"
    finally:
        engine.dispose()
