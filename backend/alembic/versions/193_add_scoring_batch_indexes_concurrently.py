"""Build exact scoring-batch recovery indexes without blocking writes.

Revision ID: 193_scoring_batch_indexes
Revises: 192_scoring_batch_job_owner
Create Date: 2026-07-19
"""

from __future__ import annotations

from typing import NamedTuple

import sqlalchemy as sa
from alembic import op


revision = "193_scoring_batch_indexes"
down_revision = "192_scoring_batch_job_owner"
branch_labels = None
depends_on = None


class _IndexSpec(NamedTuple):
    name: str
    keys: tuple[str, ...]
    predicate: str
    catalog_predicate: str
    unique: bool


_LOOKUP_INDEX = _IndexSpec(
    name="ix_cv_score_jobs_batch_run_app_attempt",
    keys=("batch_run_id", "application_id", "id"),
    predicate="batch_run_id IS NOT NULL",
    catalog_predicate="batch_run_id IS NOT NULL",
    unique=False,
)
_ACTIVE_INDEX = _IndexSpec(
    name="uq_cv_score_jobs_batch_run_app_active",
    keys=("batch_run_id", "application_id"),
    predicate=("batch_run_id IS NOT NULL AND status IN ('pending', 'running')"),
    catalog_predicate=(
        "batch_run_id IS NOT NULL AND (status::text = ANY "
        "(ARRAY['pending'::character varying, "
        "'running'::character varying]::text[]))"
    ),
    unique=True,
)
_INDEXES = (_LOOKUP_INDEX, _ACTIVE_INDEX)


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postgres_index_is_exact(
    bind: sa.engine.Connection,
    spec: _IndexSpec,
) -> bool | None:
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
            {"index_name": spec.name},
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
        and bool(state["indisunique"]) is spec.unique
        and not state["indisprimary"]
        and not state["indisexclusion"]
        and int(state["indnkeyatts"]) == len(spec.keys)
        and int(state["indnatts"]) == len(spec.keys)
        and str(state["table_name"]) == "cv_score_jobs"
        and str(state["table_schema"]) == str(state["current_schema"])
        and str(state["access_method"]) == "btree"
        and tuple(str(value) for value in state["key_expressions"]) == spec.keys
        and _normalized_sql(state["predicate"])
        == _normalized_sql(spec.catalog_predicate)
    )


def _create_sql(spec: _IndexSpec, *, if_not_exists: bool = True) -> str:
    uniqueness = "UNIQUE " if spec.unique else ""
    replay_guard = " IF NOT EXISTS" if if_not_exists else ""
    return (
        f"CREATE {uniqueness}INDEX CONCURRENTLY{replay_guard} {spec.name} "
        f"ON cv_score_jobs ({', '.join(spec.keys)}) WHERE {spec.predicate}"
    )


def _upgrade_postgres() -> None:
    context = op.get_context()
    with context.autocommit_block():
        if context.as_sql:
            for spec in _INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
                op.execute(_create_sql(spec, if_not_exists=False))
            return
        bind = op.get_bind()
        for spec in _INDEXES:
            state = _postgres_index_is_exact(bind, spec)
            if state is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
            if state is not True:
                op.execute(_create_sql(spec))
            if _postgres_index_is_exact(bind, spec) is not True:
                raise RuntimeError(
                    f"Concurrent index {spec.name} does not match revision 193."
                )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _upgrade_postgres()
        return
    if dialect != "sqlite":
        raise RuntimeError(f"unsupported migration dialect: {dialect}")
    for spec in _INDEXES:
        op.create_index(
            spec.name,
            "cv_score_jobs",
            list(spec.keys),
            unique=spec.unique,
            sqlite_where=sa.text(spec.predicate),
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            for spec in reversed(_INDEXES):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
        return
    if dialect != "sqlite":
        raise RuntimeError(f"unsupported migration dialect: {dialect}")
    for spec in reversed(_INDEXES):
        op.drop_index(spec.name, table_name="cv_score_jobs")
