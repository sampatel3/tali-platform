"""Add restart-safe partial indexes used by bounded recovery scans.

Revision ID: 190_fireflies_org_index
Revises: 189_shared_family_reject_repair
Create Date: 2026-07-18
"""

from __future__ import annotations

from typing import NamedTuple

import sqlalchemy as sa
from alembic import op


revision = "190_fireflies_org_index"
down_revision = "189_shared_family_reject_repair"
branch_labels = None
depends_on = None


class _PostgresIndexSpec(NamedTuple):
    name: str
    table_name: str
    key_expressions: tuple[str, ...]
    predicate: str
    catalog_predicate: str


_FIREFLIES_INDEX = _PostgresIndexSpec(
    name="ix_organizations_fireflies_webhook_configured",
    table_name="organizations",
    key_expressions=("id",),
    predicate=(
        "fireflies_webhook_secret IS NOT NULL "
        "AND fireflies_webhook_secret <> ''"
    ),
    catalog_predicate=(
        "fireflies_webhook_secret IS NOT NULL "
        "AND fireflies_webhook_secret::text <> ''::text"
    ),
)
_ANTHROPIC_RECOVERY_INDEX = _PostgresIndexSpec(
    name="ix_anthropic_batch_jobs_known_accepted_recovery",
    table_name="anthropic_batch_jobs",
    key_expressions=("id",),
    predicate=(
        "status = 'submission_ambiguous' "
        "AND organization_id IS NOT NULL "
        "AND (context -> '_submission_claim' ->> 'version') = '2' "
        "AND (context -> '_submission_claim' ->> 'state') = "
        "'provider_accepted_anchor_finalize_failed' "
        "AND (context -> '_submission_claim' ->> 'claim_batch_id') = batch_id "
        "AND COALESCE(context -> '_submission_claim' ->> 'attempt_id', '') <> '' "
        "AND COALESCE(context -> '_submission_claim' ->> 'provider_batch_id', '') "
        "<> '' "
        "AND (context -> '_submission_claim' ->> 'provider_batch_id') <> batch_id "
        "AND (context -> '_submission_claim' ->> 'request_count') = "
        "CAST(request_count AS TEXT) "
        "AND (feature <> 'cv_parse' "
        "OR (context -> '_submission_claim' ->> 'claim_batch_id') = "
        "'claim:cv_parse:' || "
        "(context -> '_submission_claim' ->> 'request_sha256')) "
        "AND COALESCE(context -> '_submission_recovery' ->> 'state', '') "
        "NOT IN ('invalid_known_accepted_claim', 'provider_id_collision')"
    ),
    # ``pg_get_expr(..., pretty_bool=true)`` is the canonical PostgreSQL
    # rendering of the predicate above. Comparing the parsed catalog form keeps
    # a valid same-name index with different semantics from being accepted.
    catalog_predicate=(
        "status::text = 'submission_ambiguous'::text "
        "AND organization_id IS NOT NULL "
        "AND ((context -> '_submission_claim'::text) ->> 'version'::text) = "
        "'2'::text "
        "AND ((context -> '_submission_claim'::text) ->> 'state'::text) = "
        "'provider_accepted_anchor_finalize_failed'::text "
        "AND ((context -> '_submission_claim'::text) ->> "
        "'claim_batch_id'::text) = batch_id::text "
        "AND COALESCE((context -> '_submission_claim'::text) ->> "
        "'attempt_id'::text, ''::text) <> ''::text "
        "AND COALESCE((context -> '_submission_claim'::text) ->> "
        "'provider_batch_id'::text, ''::text) <> ''::text "
        "AND ((context -> '_submission_claim'::text) ->> "
        "'provider_batch_id'::text) <> batch_id::text "
        "AND ((context -> '_submission_claim'::text) ->> "
        "'request_count'::text) = request_count::text "
        "AND (feature::text <> 'cv_parse'::text "
        "OR ((context -> '_submission_claim'::text) ->> "
        "'claim_batch_id'::text) = ('claim:cv_parse:'::text || "
        "((context -> '_submission_claim'::text) ->> "
        "'request_sha256'::text))) "
        "AND (COALESCE((context -> '_submission_recovery'::text) ->> "
        "'state'::text, ''::text) <> ALL "
        "(ARRAY['invalid_known_accepted_claim'::text, "
        "'provider_id_collision'::text]))"
    ),
)
_POSTGRES_INDEXES = (_FIREFLIES_INDEX, _ANTHROPIC_RECOVERY_INDEX)


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postgres_index_is_exact(
    bind: sa.engine.Connection,
    spec: _PostgresIndexSpec,
) -> bool | None:
    """Return whether a named index exactly matches, or ``None`` if absent.

    PostgreSQL can retain an invalid index after an interrupted concurrent
    build. ``CREATE INDEX ... IF NOT EXISTS`` would silently accept that
    unusable residue. It also accepts a valid same-name index with the wrong
    table, key, uniqueness, access method, included columns, or predicate, so
    every definition field is checked before a retry may skip DDL.
    """

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
        and not state["indisunique"]
        and not state["indisprimary"]
        and not state["indisexclusion"]
        and int(state["indnkeyatts"]) == len(spec.key_expressions)
        and int(state["indnatts"]) == len(spec.key_expressions)
        and str(state["table_name"]) == spec.table_name
        and str(state["table_schema"]) == str(state["current_schema"])
        and str(state["access_method"]) == "btree"
        and tuple(str(value) for value in state["key_expressions"])
        == spec.key_expressions
        and _normalized_sql(state["predicate"])
        == _normalized_sql(spec.catalog_predicate)
    )


def _create_index_sql(
    spec: _PostgresIndexSpec,
    *,
    if_not_exists: bool = True,
) -> str:
    columns = ", ".join(spec.key_expressions)
    replay_guard = " IF NOT EXISTS" if if_not_exists else ""
    return (
        f"CREATE INDEX CONCURRENTLY{replay_guard} {spec.name} "
        f"ON {spec.table_name} ({columns}) WHERE {spec.predicate}"
    )


def _upgrade_postgres() -> None:
    context = op.get_context()
    with context.autocommit_block():
        if context.as_sql:
            # Offline SQL cannot inspect pg_index, so replace every same-name
            # index unconditionally.  IF NOT EXISTS alone could silently retain
            # an invalid, not-ready, or wrong-definition index while Alembic
            # still stamps this revision as complete.
            for spec in _POSTGRES_INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
                op.execute(_create_index_sql(spec, if_not_exists=False))
            return

        bind = op.get_bind()
        for spec in _POSTGRES_INDEXES:
            state = _postgres_index_is_exact(bind, spec)
            if state is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
            if state is not True:
                op.execute(_create_index_sql(spec))
            if _postgres_index_is_exact(bind, spec) is not True:
                raise RuntimeError(
                    f"Concurrent index {spec.name} does not match revision 190."
                )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _upgrade_postgres()
        return

    op.create_index(
        _FIREFLIES_INDEX.name,
        "organizations",
        ["id"],
        unique=False,
        if_not_exists=True,
        sqlite_where=sa.text(_FIREFLIES_INDEX.predicate),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for spec in reversed(_POSTGRES_INDEXES):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {spec.name}")
        return

    op.drop_index(
        _FIREFLIES_INDEX.name,
        table_name="organizations",
        if_exists=True,
    )
