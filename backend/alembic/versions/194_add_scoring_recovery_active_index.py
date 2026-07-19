"""Index the bounded active scoring-recovery working set without blocking writes.

Revision ID: 194_scoring_recovery_index
Revises: 193_scoring_batch_indexes
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "194_scoring_recovery_index"
down_revision = "193_scoring_batch_indexes"
branch_labels = None
depends_on = None


_INDEX_NAME = "ix_background_job_runs_scoring_recovery_active"
_KEYS = ("scope_kind", "id")
_PREDICATE = (
    "kind = 'scoring_batch' AND finished_at IS NULL "
    "AND status IN ('dispatching', 'queued', 'running', 'cancelling')"
)
_CATALOG_PREDICATE = (
    "kind::text = 'scoring_batch'::text AND finished_at IS NULL "
    "AND (status::text = ANY (ARRAY['dispatching'::character varying, "
    "'queued'::character varying, 'running'::character varying, "
    "'cancelling'::character varying]::text[]))"
)


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postgres_index_is_exact(bind: sa.engine.Connection) -> bool | None:
    state = (
        bind.execute(
            sa.text(
                """
                SELECT
                    index_state.indisvalid,
                    index_state.indisready,
                    index_state.indislive,
                    index_state.indisunique,
                    index_state.indisprimary,
                    index_state.indisexclusion,
                    index_state.indnkeyatts,
                    index_state.indnatts,
                    table_class.relname AS table_name,
                    table_namespace.nspname AS table_schema,
                    current_schema() AS current_schema,
                    access_method.amname AS access_method,
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
                JOIN pg_namespace AS namespace
                  ON namespace.oid = index_class.relnamespace
                JOIN pg_index AS index_state
                  ON index_state.indexrelid = index_class.oid
                JOIN pg_class AS table_class
                  ON table_class.oid = index_state.indrelid
                JOIN pg_namespace AS table_namespace
                  ON table_namespace.oid = table_class.relnamespace
                JOIN pg_am AS access_method
                  ON access_method.oid = index_class.relam
                WHERE namespace.nspname = current_schema()
                  AND index_class.relname = :index_name
                """
            ),
            {"index_name": _INDEX_NAME},
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        return None
    return bool(
        state["indisvalid"]
        and state["indisready"]
        and state["indislive"]
        and not state["indisunique"]
        and not state["indisprimary"]
        and not state["indisexclusion"]
        and int(state["indnkeyatts"]) == len(_KEYS)
        and int(state["indnatts"]) == len(_KEYS)
        and str(state["table_name"]) == "background_job_runs"
        and str(state["table_schema"]) == str(state["current_schema"])
        and str(state["access_method"]) == "btree"
        and tuple(str(value) for value in state["key_expressions"]) == _KEYS
        and _normalized_sql(state["predicate"]) == _normalized_sql(_CATALOG_PREDICATE)
    )


def _create_sql(*, if_not_exists: bool = True) -> str:
    replay_guard = " IF NOT EXISTS" if if_not_exists else ""
    return (
        f"CREATE INDEX CONCURRENTLY{replay_guard} {_INDEX_NAME} "
        f"ON background_job_runs ({', '.join(_KEYS)}) WHERE {_PREDICATE}"
    )


def _upgrade_postgres() -> None:
    context = op.get_context()
    with context.autocommit_block():
        if context.as_sql:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
            op.execute(_create_sql(if_not_exists=False))
            return
        bind = op.get_bind()
        state = _postgres_index_is_exact(bind)
        if state is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
        if state is not True:
            op.execute(_create_sql())
        if _postgres_index_is_exact(bind) is not True:
            raise RuntimeError(
                f"Concurrent index {_INDEX_NAME} does not match revision 194."
            )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _upgrade_postgres()
        return
    if dialect != "sqlite":
        raise RuntimeError(f"unsupported migration dialect: {dialect}")
    op.create_index(
        _INDEX_NAME,
        "background_job_runs",
        list(_KEYS),
        unique=False,
        sqlite_where=sa.text(_PREDICATE),
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
        return
    if dialect != "sqlite":
        raise RuntimeError(f"unsupported migration dialect: {dialect}")
    op.drop_index(_INDEX_NAME, table_name="background_job_runs")
