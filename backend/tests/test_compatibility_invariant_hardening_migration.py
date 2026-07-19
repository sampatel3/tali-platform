"""Restart and evidence contracts for compatibility invariant hardening."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.scripts.compatibility_invariant_validation import (
    SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE,
    SQLITE_RELATED_HISTORY_TRIGGER_SQL,
    WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
    validate_sqlite_related_history_guards,
    validate_workspace_pause_exact_evidence,
)
from app.scripts.database_migrate import (
    MigrationSafetyError,
    MigrationValidationError,
    _alembic_config,
    _preflight_database,
    migrate_database,
)


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "195_harden_compatibility_invariants.py"
)
_SAFE_SPLIT_HEADS = (
    "173_reliable_integration_delivery",
    "174_related_role_workflow",
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "compatibility_invariant_hardening_195",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(database_url: str, revision: str) -> None:
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.upgrade(_alembic_config(), revision)


def _script_directory():
    return ScriptDirectory.from_config(_alembic_config())


def test_preflight_accepts_only_the_committed_workspace_split_frontier() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text("CREATE TABLE alembic_version (version_num VARCHAR(255))")
            )
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                [{"revision": revision} for revision in _SAFE_SPLIT_HEADS],
            )
            connection.commit()
            assert _preflight_database(connection, _script_directory()) == "versioned"

            connection.execute(sa.text("DELETE FROM alembic_version"))
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                [
                    {"revision": "173_reliable_integration_delivery"},
                    {"revision": "175_workspace_bulk_role_pause"},
                ],
            )
            connection.commit()
            with pytest.raises(MigrationSafetyError, match="exactly one"):
                _preflight_database(connection, _script_directory())
    finally:
        engine.dispose()


def test_supported_migrator_resumes_the_exact_split_head_state(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'split-head-resume.sqlite3'}"
    _upgrade(database_url, _SAFE_SPLIT_HEADS[0])
    _upgrade(database_url, _SAFE_SPLIT_HEADS[1])
    engine = sa.create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert set(
                connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalars()
            ) == set(_SAFE_SPLIT_HEADS)

        assert migrate_database(database_url) == "versioned"

        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
    finally:
        engine.dispose()


def _evidence_tables(engine: sa.Engine) -> tuple[sa.Table, sa.Table]:
    metadata = sa.MetaData()
    audits = sa.Table(
        "workspace_pause_migration_audits",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("migration_revision", sa.String, nullable=False),
        sa.Column("evidence_source", sa.String, nullable=False),
        sa.Column("evidence_quality", sa.String, nullable=False),
        sa.Column("converted_role_count", sa.Integer, nullable=False),
        sa.Column("source_role_event_ids", sa.Text, nullable=False),
        sa.Column("source_role_ids", sa.Text, nullable=False),
        sa.Column("source_workspace_event_id", sa.Integer),
        sa.Column("recorded_workspace_event_id", sa.Integer),
        sa.Column("compatibility_applied", sa.Boolean, nullable=False),
        sa.Column("control_version_before", sa.Integer, nullable=False),
        sa.Column("control_version_after", sa.Integer, nullable=False),
    )
    events = sa.Table(
        "role_change_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=False),
        sa.Column("role_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("from_version", sa.Integer, nullable=False),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("changes", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String),
    )
    metadata.create_all(engine)
    return audits, events


def _canonical_changes() -> dict[str, object]:
    return {
        "agent_paused_at": {
            "before": None,
            "after": "2026-07-16T09:00:00+00:00",
        },
        "agent_paused_reason": {
            "before": None,
            "after": "paused by workspace control",
        },
    }


def _seed_exact_audit(
    connection: sa.Connection,
    audits: sa.Table,
    events: sa.Table,
    *,
    changes_storage: str,
) -> None:
    connection.execute(
        events.insert(),
        {
            "id": 10,
            "organization_id": 1,
            "role_id": 41,
            "action": "agent_paused",
            "from_version": 3,
            "to_version": 4,
            "changes": changes_storage,
            "reason": "workspace pause migrated to role bulk control",
            "request_id": None,
        },
    )
    connection.execute(
        audits.insert(),
        {
            "id": 20,
            "organization_id": 1,
            "migration_revision": "182_workspace_pause_compat_audit",
            "evidence_source": "published_175_role_events",
            "evidence_quality": "exact",
            "converted_role_count": 1,
            "source_role_event_ids": "[10]",
            "source_role_ids": "[41]",
            "source_workspace_event_id": None,
            "recorded_workspace_event_id": None,
            "compatibility_applied": False,
            "control_version_before": 7,
            "control_version_after": 7,
        },
    )


def test_canonical_workspace_pause_evidence_passes_both_validators() -> None:
    engine = sa.create_engine("sqlite://")
    audits, events = _evidence_tables(engine)
    try:
        with engine.begin() as connection:
            _seed_exact_audit(
                connection,
                audits,
                events,
                changes_storage=json.dumps(_canonical_changes()),
            )
            validate_workspace_pause_exact_evidence(connection)
            _migration()._validate_workspace_pause_exact_evidence(connection)
    finally:
        engine.dispose()


def test_workspace_pause_role_event_lookup_uses_bounded_batches() -> None:
    engine = sa.create_engine("sqlite://")
    audits, events = _evidence_tables(engine)
    migration = _migration()
    event_ids = list(range(10, 511))
    role_ids = list(range(41, 542))
    batch_sizes: list[int] = []

    def record_event_lookup_batch(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.split())
        if "FROM role_change_events WHERE id IN" in normalized:
            assert isinstance(parameters, tuple)
            batch_sizes.append(len(parameters))

    sa.event.listen(engine, "before_cursor_execute", record_event_lookup_batch)
    try:
        with engine.begin() as connection:
            canonical_changes = json.dumps(_canonical_changes())
            _seed_exact_audit(
                connection,
                audits,
                events,
                changes_storage=canonical_changes,
            )
            connection.execute(
                events.insert(),
                [
                    {
                        "id": event_id,
                        "organization_id": 1,
                        "role_id": role_id,
                        "action": "agent_paused",
                        "from_version": 3,
                        "to_version": 4,
                        "changes": canonical_changes,
                        "reason": "workspace pause migrated to role bulk control",
                        "request_id": None,
                    }
                    for event_id, role_id in zip(
                        event_ids[1:], role_ids[1:], strict=True
                    )
                ],
            )
            connection.execute(
                audits.update().values(
                    converted_role_count=len(event_ids),
                    source_role_event_ids=json.dumps(event_ids),
                    source_role_ids=json.dumps(role_ids),
                )
            )

            validate_workspace_pause_exact_evidence(connection)
            migration._validate_workspace_pause_exact_evidence(connection)

        assert batch_sizes == [500, 1, 500, 1]
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_event_lookup_batch)
        engine.dispose()


def test_applied_compatibility_event_is_cross_checked() -> None:
    engine = sa.create_engine("sqlite://")
    audits, events = _evidence_tables(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    CREATE TABLE workspace_agent_control_events (
                        id INTEGER PRIMARY KEY,
                        organization_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        from_version INTEGER NOT NULL,
                        to_version INTEGER NOT NULL,
                        request_id TEXT
                    )
                    """
                )
            )
            _seed_exact_audit(
                connection,
                audits,
                events,
                changes_storage=json.dumps(_canonical_changes()),
            )
            connection.execute(
                audits.update().values(
                    recorded_workspace_event_id=30,
                    compatibility_applied=True,
                    control_version_after=8,
                )
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO workspace_agent_control_events (
                        id, organization_id, action, from_version, to_version, request_id
                    ) VALUES (30, 1, 'migrated', 7, 8,
                              'migration:182_workspace_pause_compat_audit:1')
                    """
                )
            )
            validate_workspace_pause_exact_evidence(connection)
            _migration()._validate_workspace_pause_exact_evidence(connection)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "changes_storage",
    [
        "{not-json",
        json.dumps(
            {
                "agent_paused_reason": {
                    "after": "paused by workspace control",
                }
            }
        ),
        json.dumps(
            {
                **_canonical_changes(),
                "agent_paused_at": {"before": None, "after": "2026-07-16"},
            }
        ),
        json.dumps(json.dumps(_canonical_changes())),
    ],
    ids=["malformed", "partial", "date-only", "double-encoded"],
)
def test_noncanonical_workspace_pause_evidence_fails_closed_without_payload_leak(
    changes_storage: str,
) -> None:
    engine = sa.create_engine("sqlite://")
    audits, events = _evidence_tables(engine)
    try:
        with engine.begin() as connection:
            _seed_exact_audit(
                connection,
                audits,
                events,
                changes_storage=changes_storage,
            )
            with pytest.raises(
                MigrationValidationError,
                match=WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
            ) as runtime_error:
                validate_workspace_pause_exact_evidence(connection)
            assert changes_storage not in str(runtime_error.value)

            with pytest.raises(
                MigrationValidationError,
                match=WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
            ) as migration_error:
                _migration()._validate_workspace_pause_exact_evidence(connection)
            assert changes_storage not in str(migration_error.value)
    finally:
        engine.dispose()


def test_contradictory_exact_audit_claim_fails_closed() -> None:
    engine = sa.create_engine("sqlite://")
    audits, events = _evidence_tables(engine)
    try:
        with engine.begin() as connection:
            _seed_exact_audit(
                connection,
                audits,
                events,
                changes_storage=json.dumps(_canonical_changes()),
            )
            connection.execute(audits.update().values(evidence_quality="limited"))
            with pytest.raises(
                MigrationValidationError,
                match=WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
            ):
                validate_workspace_pause_exact_evidence(connection)
            with pytest.raises(
                MigrationValidationError,
                match=WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
            ):
                _migration()._validate_workspace_pause_exact_evidence(connection)
            connection.execute(
                audits.update().values(
                    evidence_quality="exact",
                    compatibility_applied=True,
                    control_version_after=8,
                )
            )
            with pytest.raises(
                MigrationValidationError,
                match=WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE,
            ):
                validate_workspace_pause_exact_evidence(connection)
    finally:
        engine.dispose()


def _related_history_tables(engine: sa.Engine) -> tuple[sa.Table, sa.Table]:
    metadata = sa.MetaData()
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ats_owner_role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
        ),
    )
    evaluations = sa.Table(
        "sister_role_evaluations",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    metadata.create_all(engine)
    return roles, evaluations


def test_revision_195_adds_restart_safe_guards_without_touching_old_guards() -> None:
    engine = sa.create_engine("sqlite://")
    roles, evaluations = _related_history_tables(engine)
    migration = _migration()
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("PRAGMA foreign_keys=ON"))
            connection.execute(
                sa.text(
                    "CREATE TRIGGER preserve_owner_role_related_history "
                    "BEFORE DELETE ON roles BEGIN SELECT 1; END"
                )
            )
            old_definition = connection.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'preserve_owner_role_related_history'"
                )
            ).scalar_one()
            migration.op = Operations(MigrationContext.configure(connection))

            migration._install_sqlite_related_history_guards()
            connection.rollback()
            migration._install_sqlite_related_history_guards()
            validate_sqlite_related_history_guards(connection)

            assert (
                connection.execute(
                    sa.text(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = 'preserve_owner_role_related_history'"
                    )
                ).scalar_one()
                == old_definition
            )
            names = set(
                connection.execute(
                    sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                ).scalars()
            )
            assert set(SQLITE_RELATED_HISTORY_TRIGGER_SQL) <= names

            connection.execute(
                roles.insert(),
                [
                    {"id": 1, "ats_owner_role_id": None},
                    {"id": 2, "ats_owner_role_id": 1},
                ],
            )
            connection.execute(evaluations.insert(), {"id": 3, "role_id": 2})
            connection.commit()
            with pytest.raises(IntegrityError):
                connection.execute(roles.delete().where(roles.c.id == 1))
            connection.rollback()
            with pytest.raises(IntegrityError):
                connection.execute(roles.delete().where(roles.c.id == 2))
    finally:
        engine.dispose()


def test_revision_195_and_runtime_reject_wrong_same_name_guard() -> None:
    engine = sa.create_engine("sqlite://")
    _related_history_tables(engine)
    migration = _migration()
    guard_name = next(iter(SQLITE_RELATED_HISTORY_TRIGGER_SQL))
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    f"CREATE TRIGGER {guard_name} "
                    "BEFORE DELETE ON roles BEGIN SELECT 1; END"
                )
            )
            migration.op = Operations(MigrationContext.configure(connection))
            with pytest.raises(
                MigrationValidationError,
                match=SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE,
            ):
                migration._install_sqlite_related_history_guards()
            with pytest.raises(
                MigrationValidationError,
                match=SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE,
            ):
                validate_sqlite_related_history_guards(connection)
    finally:
        engine.dispose()


def test_supported_migrator_rejects_missing_revision_195_guard(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'missing-v195-guard.sqlite3'}"
    assert migrate_database(database_url) == "empty"
    engine = sa.create_engine(database_url, poolclass=NullPool)
    guard_name = next(iter(SQLITE_RELATED_HISTORY_TRIGGER_SQL))
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f"DROP TRIGGER {guard_name}"))
        with pytest.raises(
            MigrationValidationError,
            match=SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE,
        ):
            migrate_database(database_url)
    finally:
        engine.dispose()
