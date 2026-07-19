"""Exact post-migration model and scoring-ownership schema checks."""

from __future__ import annotations

from sqlalchemy import Connection, inspect, text


SCORING_BATCH_LOOKUP_INDEX = "ix_cv_score_jobs_batch_run_app_attempt"
SCORING_BATCH_ACTIVE_INDEX = "uq_cv_score_jobs_batch_run_app_active"
SCORING_BATCH_FOREIGN_KEY = "fk_cv_score_jobs_batch_run_id"
SCORING_RECOVERY_INDEX = "ix_background_job_runs_scoring_recovery_active"


class MigrationValidationError(RuntimeError):
    """Raised when the resulting schema does not satisfy the deploy contract."""


def validate_model_schema(
    connection: Connection, *, default_schema: str | None
) -> None:
    """Require every table and column declared by current model metadata."""

    # Importing all models is intentionally deferred until after preflight, so
    # an unsafe partial schema is rejected before application imports can have
    # any database side effects.
    import app.models  # noqa: F401
    from app.platform.database import Base

    inspector = inspect(connection)
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.fullname):
        schema = table.schema or default_schema
        if not inspector.has_table(table.name, schema=schema):
            missing_tables.append(table.fullname)
            continue
        actual_columns = {
            str(column["name"])
            for column in inspector.get_columns(table.name, schema=schema)
        }
        missing_columns.extend(
            f"{table.fullname}.{column.name}"
            for column in table.columns
            if column.name not in actual_columns
        )

    if missing_tables or missing_columns:
        details: list[str] = []
        if missing_tables:
            details.append("missing tables: " + ", ".join(missing_tables))
        if missing_columns:
            details.append("missing columns: " + ", ".join(missing_columns))
        raise MigrationValidationError(
            "Migrated schema does not satisfy current model metadata ("
            + "; ".join(details)
            + ")."
        )


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def validate_scoring_batch_ownership_contract(connection: Connection) -> None:
    """Fail closed when the rev192-194 scoring schema is absent or drifted."""

    inspector = inspect(connection)
    foreign_keys = {
        str(foreign_key.get("name") or ""): foreign_key
        for foreign_key in inspector.get_foreign_keys("cv_score_jobs")
    }
    batch_foreign_key = foreign_keys.get(SCORING_BATCH_FOREIGN_KEY)
    if (
        batch_foreign_key is None
        or batch_foreign_key.get("constrained_columns") != ["batch_run_id"]
        or batch_foreign_key.get("referred_table") != "background_job_runs"
        or batch_foreign_key.get("referred_columns") != ["id"]
        or str(batch_foreign_key.get("options", {}).get("ondelete") or "").upper()
        != "SET NULL"
    ):
        raise MigrationValidationError(
            "Scoring-batch ownership foreign key is missing or drifted."
        )

    if connection.dialect.name == "postgresql":
        validated = connection.execute(
            text(
                """
                SELECT constraint_row.convalidated
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cv_score_jobs'
                  AND constraint_row.conname = :constraint_name
                  AND constraint_row.contype = 'f'
                  AND constraint_row.confdeltype = 'n'
                """
            ),
            {"constraint_name": SCORING_BATCH_FOREIGN_KEY},
        ).scalar_one_or_none()
        if validated is not True:
            raise MigrationValidationError(
                "Scoring-batch ownership foreign key is not validated."
            )
        return

    indexes = {
        str(index.get("name") or ""): index
        for index in inspector.get_indexes("cv_score_jobs")
    }
    expected = {
        SCORING_BATCH_LOOKUP_INDEX: (
            ["batch_run_id", "application_id", "id"],
            False,
            "where batch_run_id is not null",
        ),
        SCORING_BATCH_ACTIVE_INDEX: (
            ["batch_run_id", "application_id"],
            True,
            "where batch_run_id is not null and status in ('pending', 'running')",
        ),
    }
    definitions = {
        str(row["name"]): _normalized_sql(row["sql"])
        for row in connection.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'index' AND name IN (:lookup, :active)"
            ),
            {
                "lookup": SCORING_BATCH_LOOKUP_INDEX,
                "active": SCORING_BATCH_ACTIVE_INDEX,
            },
        ).mappings()
    }
    for name, (columns, unique, predicate) in expected.items():
        state = indexes.get(name)
        if (
            state is None
            or state.get("column_names") != columns
            or bool(state.get("unique")) is not unique
            or predicate not in definitions.get(name, "")
        ):
            raise MigrationValidationError(
                f"SQLite scoring-batch index is missing or drifted: {name}."
            )

    recovery_state = {
        str(index.get("name") or ""): index
        for index in inspector.get_indexes("background_job_runs")
    }.get(SCORING_RECOVERY_INDEX)
    recovery_definition = _normalized_sql(
        connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
            {"name": SCORING_RECOVERY_INDEX},
        ).scalar_one_or_none()
    )
    recovery_predicate = _normalized_sql(
        "kind = 'scoring_batch' and finished_at is null "
        "and status in ('dispatching', 'queued', 'running', 'cancelling')"
    )
    _prefix, separator, actual_recovery_predicate = recovery_definition.partition(
        " where "
    )
    if (
        recovery_state is None
        or recovery_state.get("column_names") != ["scope_kind", "id"]
        or bool(recovery_state.get("unique"))
        or not separator
        or actual_recovery_predicate != recovery_predicate
    ):
        raise MigrationValidationError(
            "SQLite scoring recovery index is missing or drifted."
        )
