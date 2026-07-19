"""Apply the canonical Alembic chain with fail-closed bootstrap checks.

The only supported empty-database bootstrap is the Alembic chain beginning at
``000_initial_schema``.  ``Base.metadata.create_all()`` followed by ``stamp``
is intentionally not used: it would omit migration-only PostgreSQL triggers,
search indexes, extensions, constraints, enum changes, and data migrations.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.scripts import compatibility_invariant_validation as compatibility_invariants
from app.scripts.database_schema_validation import (
    MigrationValidationError,
    validate_model_schema,
    validate_scoring_batch_ownership_contract,
)
from app.scripts.postgres_concurrent_indexes import (
    drifted_postgres_indexes,
    postgres_session_lock_timeout,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

# Session-level lock shared by every supported migration entry point.  The
# value is a stable signed bigint (ASCII-ish "TALIMIGR"), scoped to one DB.
POSTGRES_ADVISORY_LOCK_ID = 0x54414C494D494752
DEFAULT_LOCK_TIMEOUT_SECONDS = 300.0
LOCK_POLL_INTERVAL_SECONDS = 0.25
PUBLISHED_WORKSPACE_PAUSE_REVISION = "175_workspace_bulk_role_pause"
WORKSPACE_PAUSE_PREDECESSOR_REVISION = "174_related_role_workflow"
POSTGRES_CONCURRENT_INDEX_REVISION = "190_fireflies_org_index"
POSTGRES_CONCURRENT_INDEX_PREDECESSOR_REVISION = "189_shared_family_reject_repair"
POSTGRES_SCORING_INDEX_REVISION = "193_scoring_batch_indexes"
POSTGRES_SCORING_INDEX_PREDECESSOR_REVISION = "192_scoring_batch_job_owner"
POSTGRES_SCORING_RECOVERY_INDEX_REVISION = "194_scoring_recovery_index"
POSTGRES_SCORING_RECOVERY_INDEX_PREDECESSOR_REVISION = "193_scoring_batch_indexes"
POSTGRES_CONCURRENT_INDEX_STEPS = (
    (
        POSTGRES_CONCURRENT_INDEX_REVISION,
        POSTGRES_CONCURRENT_INDEX_PREDECESSOR_REVISION,
    ),
    (
        POSTGRES_SCORING_INDEX_REVISION,
        POSTGRES_SCORING_INDEX_PREDECESSOR_REVISION,
    ),
    (
        POSTGRES_SCORING_RECOVERY_INDEX_REVISION,
        POSTGRES_SCORING_RECOVERY_INDEX_PREDECESSOR_REVISION,
    ),
)
SUPPORTED_DATABASE_DIALECTS = frozenset({"postgresql", "sqlite"})
POSTGRES_REQUIRED_INDEXES = frozenset(
    {
        "ix_candidates_search_skills_trgm",
        "ix_candidates_search_experience_trgm",
        "ix_candidates_search_profile_trgm",
        "ix_candidate_applications_cv_fts",
        "ix_candidates_cv_fts",
        "ix_claude_call_log_batch_result_lookup",
        "ix_cv_score_jobs_batch_run_app_attempt",
        "ix_anthropic_batch_jobs_known_accepted_recovery",
        "ix_background_job_runs_scoring_recovery_active",
        "ix_organizations_fireflies_webhook_configured",
        "ix_usage_events_batch_id",
        "uq_cv_score_jobs_batch_run_app_active",
    }
)
POSTGRES_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_candidate_application_events_no_update",
        "role_change_events_append_only",
        "workspace_pause_migration_audits_append_only",
        "trg_anthropic_batch_receipt_immutable",
        "enforce_shared_family_auto_reject_v189",
    }
)
POSTGRES_REQUIRED_RESTRICT_FOREIGN_KEYS = frozenset(
    {
        "roles_ats_owner_role_id_fkey",
        "sister_role_evaluations_role_id_fkey",
    }
)
POSTGRES_REQUIRED_ASSESSMENT_STATUSES = frozenset(
    {
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "EXPIRED",
        "COMPLETED_DUE_TO_TIMEOUT",
    }
)
SHARED_FAMILY_TRIGGER_NAME = "enforce_shared_family_auto_reject_v189"
SHARED_FAMILY_FUNCTION_NAME = "enforce_shared_family_auto_reject_v189"
SQLITE_SHARED_FAMILY_TRIGGER_NAMES = frozenset(
    {
        "enforce_shared_family_auto_reject_insert_v189",
        "enforce_shared_family_auto_reject_update_v189",
    }
)
SHARED_FAMILY_TRIGGER_UPDATE_COLUMNS = frozenset(
    {
        "organization_id",
        "role_kind",
        "ats_owner_role_id",
        "deleted_at",
        "auto_reject",
        "auto_reject_pre_screen",
    }
)


class MigrationSafetyError(RuntimeError):
    """Raised before migrations when the database cannot be upgraded safely."""


def _log(message: str) -> None:
    print(f"[database-migrate] {message}", flush=True)


def _alembic_config() -> Config:
    if not ALEMBIC_INI.is_file():
        raise MigrationSafetyError(f"Alembic config is missing at {ALEMBIC_INI}.")
    config = Config(str(ALEMBIC_INI))
    # This migrator runs inside API/worker startup and test processes. Their
    # structured root handler is authoritative; Alembic's standalone INI
    # formatter would replace it, suppress INFO evidence, and bypass the
    # platform's secret-safe formatter. The Alembic CLI keeps its default.
    config.attributes["configure_logger"] = False
    return config


def _database_url(config: Config) -> str:
    url = str(
        os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
    ).strip()
    if not url:
        raise MigrationSafetyError(
            "DATABASE_URL is empty and Alembic has no fallback URL."
        )
    # Railway can expose the legacy scheme while SQLAlchemy 2 expects the
    # explicit dialect name.  Alembic and this preflight must target one URL.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _require_supported_database_dialect(connection: Connection) -> None:
    dialect = str(connection.dialect.name)
    if dialect not in SUPPORTED_DATABASE_DIALECTS:
        raise MigrationSafetyError(
            "Refusing to migrate an unsupported database dialect "
            f"({dialect!r}); supported dialects are PostgreSQL and SQLite."
        )


def _schema_objects(connection: Connection) -> set[str]:
    """Return user-owned objects that make an unversioned schema non-empty."""
    if connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                """
                SELECT kind || ':' || name
                FROM (
                    SELECT CASE c.relkind
                               WHEN 'r' THEN 'table'
                               WHEN 'p' THEN 'table'
                               WHEN 'S' THEN 'sequence'
                               WHEN 'v' THEN 'view'
                               WHEN 'm' THEN 'materialized-view'
                               WHEN 'f' THEN 'foreign-table'
                           END AS kind,
                           c.relname AS name
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = current_schema()
                      AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                    UNION ALL
                    SELECT 'enum' AS kind, t.typname AS name
                    FROM pg_type AS t
                    JOIN pg_namespace AS n ON n.oid = t.typnamespace
                    WHERE n.nspname = current_schema() AND t.typtype = 'e'
                ) AS schema_objects
                WHERE kind IS NOT NULL
                """
            )
        ).scalars()
        return {str(row) for row in rows}

    inspector = inspect(connection)
    objects = {f"table:{name}" for name in inspector.get_table_names()}
    objects.update(f"view:{name}" for name in inspector.get_view_names())
    return objects


def _current_schema(connection: Connection) -> str | None:
    if connection.dialect.name != "postgresql":
        return None
    schema = connection.execute(text("SELECT current_schema()")).scalar_one_or_none()
    if not schema:
        raise MigrationSafetyError(
            "PostgreSQL search_path has no writable current schema."
        )
    return str(schema)


def _has_version_table(connection: Connection) -> bool:
    if connection.dialect.name != "postgresql":
        return inspect(connection).has_table("alembic_version")
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = 'alembic_version'
                      AND relation.relkind IN ('r', 'p')
                )
                """
            )
        ).scalar_one()
    )


def _lock_timeout_seconds() -> float:
    raw_value = os.environ.get(
        "DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS",
        str(DEFAULT_LOCK_TIMEOUT_SECONDS),
    ).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise MigrationSafetyError(
            "DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS must be a positive number."
        ) from exc
    if value <= 0:
        raise MigrationSafetyError(
            "DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS must be a positive number."
        )
    return value


def _preflight_database(connection: Connection, script: ScriptDirectory) -> str:
    """Classify empty/versioned schemas and reject unsafe partial schemas."""
    if not _has_version_table(connection):
        objects = _schema_objects(connection)
        if objects:
            sample = ", ".join(sorted(objects)[:8])
            suffix = " ..." if len(objects) > 8 else ""
            raise MigrationSafetyError(
                "Refusing to migrate an unversioned non-empty schema. "
                f"Found {sample}{suffix}. Restore its Alembic revision or use a "
                "new empty database; no migration DDL was applied."
            )
        return "empty"

    version_table = "alembic_version"
    schema = _current_schema(connection)
    if schema is not None:
        quote = connection.dialect.identifier_preparer.quote
        version_table = f"{quote(schema)}.{quote(version_table)}"
    rows = list(connection.execute(text(f"SELECT version_num FROM {version_table}")))
    revisions = [str(row[0]).strip() if row[0] is not None else "" for row in rows]
    if not compatibility_invariants.alembic_version_rows_are_resumable(revisions):
        raise MigrationSafetyError(
            "Refusing to migrate: alembic_version must contain exactly one "
            "non-empty revision row, except the exact resumable compatibility "
            "split frontier."
        )
    if not compatibility_invariants.alembic_version_rows_are_known(revisions, script):
        raise MigrationSafetyError(
            "Refusing to migrate: alembic_version names a revision absent from "
            "this release."
        )
    return "versioned"


@contextmanager
def _migration_lock(
    engine: Engine, timeout_seconds: float | None = None
) -> Iterator[Connection]:
    """Hold a PostgreSQL session lock through preflight, upgrade, and validation."""
    with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            yield connection
            return

        timeout = (
            timeout_seconds if timeout_seconds is not None else _lock_timeout_seconds()
        )
        if timeout <= 0:
            raise MigrationSafetyError(
                "Database migration lock timeout must be positive."
            )
        deadline = time.monotonic() + timeout
        _log(f"Waiting for the database migration lock (timeout={timeout:g}s)...")
        while True:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                ).scalar_one()
            )
            # Advisory locks are session-scoped, so ending the read transaction
            # avoids sitting idle-in-transaction while an upgrade runs or waits.
            connection.rollback()
            if acquired:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MigrationSafetyError(
                    "Timed out waiting for the database migration lock after "
                    f"{timeout:g} seconds."
                )
            time.sleep(min(LOCK_POLL_INTERVAL_SECONDS, remaining))
        _log("Database migration lock acquired.")
        try:
            yield connection
        finally:
            # A failed validation query can leave the transaction aborted.  A
            # rollback makes the unlock callable; invalidating on unlock error
            # closes the DBAPI session, which also releases session-level locks.
            try:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": POSTGRES_ADVISORY_LOCK_ID},
                )
                connection.commit()
            except Exception:
                connection.invalidate()


@contextmanager
def _alembic_database_url(database_url: str) -> Iterator[None]:
    """Make Alembic env.py use the already-preflighted URL exactly."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _validate_postgres_concurrent_indexes(connection: Connection) -> None:
    """Require every concurrent migration index to match its exact contract."""

    drifted = drifted_postgres_indexes(connection)
    if drifted:
        raise MigrationValidationError(
            "PostgreSQL concurrent index definitions do not match: "
            + ", ".join(drifted)
            + "."
        )


def _validate_shared_family_data_invariant(connection: Connection) -> None:
    counts = (
        connection.execute(
            text(
                """
            SELECT
                (
                    SELECT count(*)
                    FROM roles AS candidate
                    WHERE candidate.deleted_at IS NULL
                      AND (
                          COALESCE(candidate.auto_reject, false)
                          OR COALESCE(candidate.auto_reject_pre_screen, false)
                      )
                      AND (
                          candidate.role_kind = 'sister'
                          OR EXISTS (
                              SELECT 1
                              FROM roles AS related
                              WHERE related.ats_owner_role_id = candidate.id
                                AND related.role_kind = 'sister'
                                AND related.deleted_at IS NULL
                          )
                      )
                ) AS unsafe_live_roles,
                (
                    SELECT count(*)
                    FROM roles AS related
                    JOIN roles AS owner ON owner.id = related.ats_owner_role_id
                    WHERE related.role_kind = 'sister'
                      AND related.organization_id <> owner.organization_id
                ) AS cross_tenant_edges
            """
            )
        )
        .mappings()
        .one()
    )
    unsafe_live_roles = int(counts["unsafe_live_roles"] or 0)
    cross_tenant_edges = int(counts["cross_tenant_edges"] or 0)
    if unsafe_live_roles or cross_tenant_edges:
        raise MigrationValidationError(
            "Shared-family data invariant validation failed: "
            f"{unsafe_live_roles} unsafe live role(s); "
            f"{cross_tenant_edges} cross-tenant ATS owner edge(s)."
        )


def _validate_postgres_shared_family_trigger(connection: Connection) -> None:
    current_schema = _current_schema(connection)
    rows = (
        connection.execute(
            text(
                """
            SELECT
                relation.relname AS relation_name,
                trigger_row.tgenabled AS enabled,
                trigger_row.tgtype AS trigger_type,
                trigger_row.tgqual IS NULL AS has_no_when_clause,
                trigger_row.tgnargs AS trigger_argument_count,
                procedure_row.proname AS function_name,
                procedure_namespace.nspname AS function_schema,
                language_row.lanname AS function_language,
                procedure_row.provolatile AS function_volatility,
                procedure_row.prosecdef AS function_security_definer,
                procedure_row.pronargs AS function_argument_count,
                pg_get_function_result(procedure_row.oid) AS function_result,
                procedure_row.proconfig AS function_config,
                procedure_row.prosrc AS function_source,
                ARRAY(
                    SELECT attribute.attname
                    FROM unnest(trigger_row.tgattr::smallint[])
                         WITH ORDINALITY AS trigger_column(attnum, position)
                    JOIN pg_attribute AS attribute
                      ON attribute.attrelid = relation.oid
                     AND attribute.attnum = trigger_column.attnum
                    ORDER BY trigger_column.position
                ) AS update_columns
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid
            JOIN pg_namespace AS relation_namespace
              ON relation_namespace.oid = relation.relnamespace
            JOIN pg_proc AS procedure_row
              ON procedure_row.oid = trigger_row.tgfoid
            JOIN pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure_row.pronamespace
            JOIN pg_language AS language_row
              ON language_row.oid = procedure_row.prolang
            WHERE NOT trigger_row.tgisinternal
              AND trigger_row.tgname = :trigger_name
              AND relation_namespace.nspname = current_schema()
            """
            ),
            {"trigger_name": SHARED_FAMILY_TRIGGER_NAME},
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise MigrationValidationError(
            "Expected exactly one PostgreSQL shared-family invariant trigger "
            f"in schema {current_schema!r}; found {len(rows)}."
        )
    trigger = rows[0]
    if str(trigger["enabled"]) not in {"O", "A"}:
        raise MigrationValidationError(
            "PostgreSQL shared-family invariant trigger is not enabled for "
            "normal writes."
        )

    # BEFORE + ROW + INSERT + UPDATE. Avoid full DDL equality while still
    # rejecting a same-name trigger with a weaker event contract.
    expected_trigger_type = 1 | 2 | 4 | 16
    update_columns = {str(name) for name in (trigger["update_columns"] or [])}
    config = [str(value) for value in (trigger["function_config"] or [])]
    search_path_configured = any(
        value.startswith("search_path=")
        and str(current_schema) in value
        and "pg_temp" in value
        for value in config
    )
    structural_definition_valid = all(
        (
            trigger["relation_name"] == "roles",
            int(trigger["trigger_type"]) == expected_trigger_type,
            bool(trigger["has_no_when_clause"]),
            int(trigger["trigger_argument_count"]) == 0,
            update_columns == SHARED_FAMILY_TRIGGER_UPDATE_COLUMNS,
            trigger["function_name"] == SHARED_FAMILY_FUNCTION_NAME,
            trigger["function_schema"] == current_schema,
            trigger["function_language"] == "plpgsql",
            trigger["function_volatility"] == "v",
            trigger["function_security_definer"] is False,
            int(trigger["function_argument_count"]) == 0,
            trigger["function_result"] == "trigger",
            search_path_configured,
        )
    )
    normalized_source = _normalized_sql(trigger["function_source"])
    required_source_fragments = (
        "for update",
        "new.auto_reject",
        "new.auto_reject_pre_screen",
        "related.ats_owner_role_id = new.id",
        "owner.id = new.ats_owner_role_id",
        "related.organization_id <> new.organization_id",
        "owner.organization_id <> new.organization_id",
        "shared role families cannot enable automatic rejection",
        "related roles cannot reference an ats owner in another organization",
    )
    if not structural_definition_valid or not all(
        fragment in normalized_source for fragment in required_source_fragments
    ):
        raise MigrationValidationError(
            "PostgreSQL shared-family trigger definition does not match the "
            "required relation, events, columns, or invariant function."
        )


def _validate_sqlite_shared_family_triggers(connection: Connection) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name IN "
                "(:insert_name, :update_name)"
            ),
            {
                "insert_name": "enforce_shared_family_auto_reject_insert_v189",
                "update_name": "enforce_shared_family_auto_reject_update_v189",
            },
        )
        .mappings()
        .all()
    )
    by_name = {str(row["name"]): row for row in rows}
    missing = sorted(SQLITE_SHARED_FAMILY_TRIGGER_NAMES - set(by_name))
    if missing:
        raise MigrationValidationError(
            "SQLite shared-family invariant triggers are missing: "
            + ", ".join(missing)
            + "."
        )
    common_fragments = (
        "new.auto_reject",
        "new.auto_reject_pre_screen",
        "related.ats_owner_role_id = new.id",
        "owner.id = new.ats_owner_role_id",
        "related.organization_id <> new.organization_id",
        "owner.organization_id <> new.organization_id",
        "shared role families cannot enable automatic rejection",
        "related roles cannot reference an ats owner in another organization",
    )
    operation_fragment = {
        "enforce_shared_family_auto_reject_insert_v189": "before insert on roles",
        "enforce_shared_family_auto_reject_update_v189": (
            "before update of organization_id, role_kind, ats_owner_role_id, "
            "deleted_at, auto_reject, auto_reject_pre_screen on roles"
        ),
    }
    for name, row in by_name.items():
        definition = _normalized_sql(row["sql"])
        if (
            row["tbl_name"] != "roles"
            or operation_fragment[name] not in definition
            or not all(fragment in definition for fragment in common_fragments)
        ):
            raise MigrationValidationError(
                f"SQLite shared-family trigger definition drifted for {name}."
            )


def _validate_postgres_invariants(
    connection: Connection, script: ScriptDirectory
) -> None:
    extension_present = bool(
        connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')")
        ).scalar_one()
    )
    if not extension_present:
        raise MigrationValidationError(
            "Required PostgreSQL extension pg_trgm is missing."
        )

    indexes = {
        str(name)
        for name in connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
        ).scalars()
    }
    missing_indexes = sorted(POSTGRES_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise MigrationValidationError(
            "Required PostgreSQL search indexes are missing: "
            + ", ".join(missing_indexes)
            + "."
        )
    _validate_postgres_concurrent_indexes(connection)

    triggers = {
        str(name)
        for name in connection.execute(
            text(
                """
                SELECT trigger.tgname
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE NOT trigger.tgisinternal
                  AND namespace.nspname = current_schema()
                """
            )
        ).scalars()
    }
    missing_triggers = sorted(POSTGRES_REQUIRED_TRIGGERS - triggers)
    if missing_triggers:
        raise MigrationValidationError(
            "Required PostgreSQL invariant triggers are missing: "
            + ", ".join(missing_triggers)
            + "."
        )
    _validate_postgres_shared_family_trigger(connection)

    workspace_action_check = connection.execute(
        text(
            """
            SELECT pg_get_constraintdef(constraint_row.oid)
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation
              ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'workspace_agent_control_events'
              AND constraint_row.conname = 'ck_workspace_agent_control_events_action'
              AND constraint_row.contype = 'c'
            """
        )
    ).scalar_one_or_none()
    if not workspace_action_check or "migrated" not in str(workspace_action_check):
        raise MigrationValidationError(
            "workspace_agent_control_events action constraint does not allow "
            "the migration compatibility event."
        )

    current_schema = _current_schema(connection)
    postgres_inspector = inspect(connection)
    restrict_foreign_keys: set[str] = set()
    for table_name, column_name, referred_table, expected_name in (
        (
            "roles",
            "ats_owner_role_id",
            "roles",
            "roles_ats_owner_role_id_fkey",
        ),
        (
            "sister_role_evaluations",
            "role_id",
            "roles",
            "sister_role_evaluations_role_id_fkey",
        ),
    ):
        for foreign_key in postgres_inspector.get_foreign_keys(
            table_name,
            schema=current_schema,
        ):
            options = foreign_key.get("options") or {}
            if (
                foreign_key.get("name") == expected_name
                and foreign_key.get("constrained_columns") == [column_name]
                and foreign_key.get("referred_table") == referred_table
                and foreign_key.get("referred_columns") == ["id"]
                and str(options.get("ondelete") or "").upper() == "RESTRICT"
            ):
                restrict_foreign_keys.add(expected_name)
                break
    missing_restrict_foreign_keys = sorted(
        POSTGRES_REQUIRED_RESTRICT_FOREIGN_KEYS - restrict_foreign_keys
    )
    if missing_restrict_foreign_keys:
        raise MigrationValidationError(
            "Related-role history foreign keys are not delete-restricting: "
            + ", ".join(missing_restrict_foreign_keys)
            + "."
        )

    assessment_statuses = {
        str(label)
        for label in connection.execute(
            text(
                """
                SELECT enum.enumlabel
                FROM pg_type AS type
                JOIN pg_enum AS enum ON enum.enumtypid = type.oid
                JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace
                WHERE type.typname = 'assessmentstatus'
                  AND namespace.nspname = current_schema()
                """
            )
        ).scalars()
    }
    missing_statuses = sorted(
        POSTGRES_REQUIRED_ASSESSMENT_STATUSES - assessment_statuses
    )
    if missing_statuses:
        raise MigrationValidationError(
            "assessmentstatus is missing values: " + ", ".join(missing_statuses) + "."
        )

    version_columns = {
        str(column["name"]): column
        for column in inspect(connection).get_columns(
            "alembic_version", schema=current_schema
        )
    }
    version_column = version_columns.get("version_num")
    max_revision_length = max(
        len(revision.revision) for revision in script.walk_revisions()
    )
    configured_length = (
        getattr(version_column["type"], "length", None) if version_column else 0
    )
    if (
        configured_length is not None
        and int(configured_length or 0) < max_revision_length
    ):
        raise MigrationValidationError(
            "alembic_version.version_num is too short for this migration graph."
        )


def _validate_database(connection: Connection, script: ScriptDirectory) -> None:
    migration_options = {}
    current_schema = _current_schema(connection)
    if current_schema is not None:
        migration_options["version_table_schema"] = current_schema
    current_heads = set(
        MigrationContext.configure(
            connection, opts=migration_options
        ).get_current_heads()
    )
    expected_heads = set(script.get_heads())
    if current_heads != expected_heads:
        raise MigrationValidationError(
            "Alembic did not reach the release head "
            f"(expected {sorted(expected_heads)}, found {sorted(current_heads)})."
        )
    validate_model_schema(connection, default_schema=current_schema)
    validate_scoring_batch_ownership_contract(connection)
    compatibility_invariants.validate_workspace_pause_exact_evidence(connection)
    if connection.dialect.name == "postgresql":
        _validate_postgres_invariants(connection, script)
    else:
        _validate_sqlite_shared_family_triggers(connection)
        compatibility_invariants.validate_sqlite_related_history_guards(connection)
    _validate_shared_family_data_invariant(connection)


def _applied_revisions(connection: Connection, script: ScriptDirectory) -> set[str]:
    """Return every revision reachable from the database's current heads."""

    migration_options = {}
    current_schema = _current_schema(connection)
    if current_schema is not None:
        migration_options["version_table_schema"] = current_schema
    current_heads = MigrationContext.configure(
        connection, opts=migration_options
    ).get_current_heads()
    applied: set[str] = set()
    for head in current_heads:
        applied.update(
            revision.revision for revision in script.iterate_revisions(head, "base")
        )
    return applied


def _set_postgres_migration_lock_timeout(connection: Connection) -> None:
    """Bound DDL/data lock waits for every versioned PostgreSQL upgrade."""

    if connection.dialect.name != "postgresql":
        return
    timeout_ms = max(1, int(_lock_timeout_seconds() * 1000))
    connection.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{timeout_ms}ms"},
    )


@contextmanager
def _postgres_session_migration_lock_timeout(
    connection: Connection,
) -> Iterator[None]:
    """Use the deployment timeout for concurrent-index autocommit DDL."""

    with postgres_session_lock_timeout(
        connection,
        timeout_seconds=_lock_timeout_seconds(),
    ):
        yield


def _lock_workspace_pause_conversion_tables(connection: Connection) -> None:
    """Fence immutable migration 175 from concurrent application writers.

    PostgreSQL ``EXCLUSIVE`` still permits ordinary reads (``ACCESS SHARE``),
    but blocks both row-locking readers and writers. Taking the organization
    table first matches the runtime organization -> role lock order.
    """

    if connection.dialect.name != "postgresql":
        return
    _set_postgres_migration_lock_timeout(connection)
    try:
        connection.execute(text("LOCK TABLE organizations IN EXCLUSIVE MODE"))
        connection.execute(text("LOCK TABLE roles IN EXCLUSIVE MODE"))
    except DBAPIError as exc:
        raise MigrationSafetyError(
            "Timed out waiting to fence application writes for the published "
            "workspace-pause conversion."
        ) from exc


def migrate_database(database_url: str | None = None) -> str:
    """Safely migrate one database and return its preflight classification."""
    config = _alembic_config()
    url = database_url or _database_url(config)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    script = ScriptDirectory.from_config(config)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with _migration_lock(engine) as lock_connection:
            _require_supported_database_dialect(lock_connection)
            classification = _preflight_database(lock_connection, script)
            _log(f"Preflight passed ({classification} schema).")
            current_schema = _current_schema(lock_connection)
            if current_schema is not None:
                config.attributes["version_table_schema"] = current_schema
            # Preflight reads open a transaction. End it before handing this
            # same advisory-locked connection to Alembic.
            if lock_connection.in_transaction():
                lock_connection.rollback()
            config.attributes["connection"] = lock_connection
            try:
                with _alembic_database_url(url):
                    applied_revisions = (
                        _applied_revisions(lock_connection, script)
                        if classification == "versioned"
                        else set()
                    )
                    needs_workspace_pause_conversion = (
                        classification == "versioned"
                        and PUBLISHED_WORKSPACE_PAUSE_REVISION not in applied_revisions
                    )
                    concurrent_index_predecessor = None
                    if (
                        classification == "versioned"
                        and lock_connection.dialect.name == "postgresql"
                    ):
                        concurrent_index_predecessor = next(
                            (
                                predecessor
                                for revision, predecessor in (
                                    POSTGRES_CONCURRENT_INDEX_STEPS
                                )
                                if revision not in applied_revisions
                            ),
                            None,
                        )
                    needs_concurrent_index_revision = (
                        concurrent_index_predecessor is not None
                    )
                    if lock_connection.in_transaction():
                        lock_connection.rollback()

                    if needs_workspace_pause_conversion:
                        # Stop immediately before immutable 175. This safely
                        # crosses migration 160's autocommit block, then lets
                        # the conversion and every later revision share the
                        # write-fencing transaction below.
                        command.upgrade(config, WORKSPACE_PAUSE_PREDECESSOR_REVISION)
                        if lock_connection.in_transaction():
                            lock_connection.commit()

                    if classification == "empty":
                        # Fresh bootstrap includes migration 160's PostgreSQL
                        # autocommit block and therefore cannot be one outer
                        # transaction. It still uses the advisory-locked
                        # connection and is fully validated before success.
                        with _postgres_session_migration_lock_timeout(lock_connection):
                            command.upgrade(config, "head")
                            _validate_database(lock_connection, script)
                        if lock_connection.in_transaction():
                            lock_connection.rollback()
                    else:
                        with lock_connection.begin():
                            _set_postgres_migration_lock_timeout(lock_connection)
                            if needs_workspace_pause_conversion:
                                _log(
                                    "Fencing workspace and role writes for "
                                    "published migration 175."
                                )
                                _lock_workspace_pause_conversion_tables(lock_connection)
                            command.upgrade(
                                config,
                                concurrent_index_predecessor
                                if needs_concurrent_index_revision
                                else "head",
                            )
                            if not needs_concurrent_index_revision:
                                _validate_database(lock_connection, script)
                        if needs_concurrent_index_revision:
                            # PostgreSQL forbids CREATE INDEX CONCURRENTLY in
                            # the compatibility transaction above. Commit
                            # transactional predecessors first, then let each
                            # Alembic autocommit block build concurrent indexes
                            # while the session-level migration advisory lock
                            # remains held. A failed build leaves the last
                            # transactional predecessor stamped; every
                            # concurrent-index revision repairs its exact
                            # catalog contract safely on retry.
                            with _postgres_session_migration_lock_timeout(
                                lock_connection
                            ):
                                command.upgrade(config, "head")
                                _validate_database(lock_connection, script)
                            if lock_connection.in_transaction():
                                lock_connection.rollback()
            finally:
                config.attributes.pop("connection", None)
            _log("Migration and schema invariant validation passed.")
            return classification
    finally:
        engine.dispose()


def main() -> int:
    # The supported deployment entry point is a fresh subprocess, so it has
    # no host logger to preserve. Install the application's structured,
    # secret-safe handler before Alembic emits per-revision progress.
    from app.platform.logging import setup_logging

    setup_logging()
    try:
        migrate_database()
    except (MigrationSafetyError, MigrationValidationError) as exc:
        print(f"[database-migrate] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        # Do not echo exception text: driver errors can include connection
        # details. Alembic/SQLAlchemy logs above identify the failing operation.
        print(
            f"[database-migrate] ERROR: migration failed ({type(exc).__name__}).",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
