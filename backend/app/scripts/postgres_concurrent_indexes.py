"""Exact catalog contracts and bounded waits for concurrent migration indexes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class PostgresIndexContract:
    table_name: str
    key_expressions: tuple[str, ...]
    catalog_predicate: str
    unique: bool = False


POSTGRES_CONCURRENT_INDEX_CONTRACTS = {
    "ix_background_job_runs_scoring_recovery_active": PostgresIndexContract(
        table_name="background_job_runs",
        key_expressions=("scope_kind", "id"),
        catalog_predicate=(
            "kind::text = 'scoring_batch'::text AND finished_at IS NULL "
            "AND (status::text = ANY (ARRAY['dispatching'::character varying, "
            "'queued'::character varying, 'running'::character varying, "
            "'cancelling'::character varying]::text[]))"
        ),
    ),
    "ix_organizations_fireflies_webhook_configured": PostgresIndexContract(
        table_name="organizations",
        key_expressions=("id",),
        catalog_predicate=(
            "fireflies_webhook_secret IS NOT NULL "
            "AND fireflies_webhook_secret::text <> ''::text"
        ),
    ),
    "ix_anthropic_batch_jobs_known_accepted_recovery": PostgresIndexContract(
        table_name="anthropic_batch_jobs",
        key_expressions=("id",),
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
    ),
    "ix_cv_score_jobs_batch_run_app_attempt": PostgresIndexContract(
        table_name="cv_score_jobs",
        key_expressions=("batch_run_id", "application_id", "id"),
        catalog_predicate="batch_run_id IS NOT NULL",
    ),
    "uq_cv_score_jobs_batch_run_app_active": PostgresIndexContract(
        table_name="cv_score_jobs",
        key_expressions=("batch_run_id", "application_id"),
        catalog_predicate=(
            "batch_run_id IS NOT NULL AND (status::text = ANY "
            "(ARRAY['pending'::character varying, "
            "'running'::character varying]::text[]))"
        ),
        unique=True,
    ),
}


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postgres_index_state(
    connection: Connection,
    *,
    index_name: str,
) -> Mapping[str, object] | None:
    return (
        connection.execute(
            text(
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
            {"index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )


def drifted_postgres_indexes(
    connection: Connection,
    contracts: Mapping[str, PostgresIndexContract] | None = None,
) -> tuple[str, ...]:
    """Return named indexes that are absent, invalid, or definition-drifted."""

    effective_contracts = (
        POSTGRES_CONCURRENT_INDEX_CONTRACTS if contracts is None else contracts
    )
    drifted: list[str] = []
    for index_name, contract in effective_contracts.items():
        state = _postgres_index_state(connection, index_name=index_name)
        exact = state is not None and bool(
            state["indisvalid"]
            and state["indisready"]
            and state["indislive"]
            and bool(state["indisunique"]) is contract.unique
            and not state["indisprimary"]
            and not state["indisexclusion"]
            and int(state["indnkeyatts"]) == len(contract.key_expressions)
            and int(state["indnatts"]) == len(contract.key_expressions)
            and str(state["table_name"]) == contract.table_name
            and str(state["table_schema"]) == str(state["current_schema"])
            and str(state["access_method"]) == "btree"
            and tuple(str(value) for value in state["key_expressions"])
            == contract.key_expressions
            and _normalized_sql(state["predicate"])
            == _normalized_sql(contract.catalog_predicate)
        )
        if not exact:
            drifted.append(index_name)
    return tuple(sorted(drifted))


@contextmanager
def postgres_session_lock_timeout(
    connection: Connection,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    """Bound autocommit DDL waits, then restore the prior session setting."""

    if connection.dialect.name != "postgresql":
        yield
        return
    if connection.in_transaction():
        raise RuntimeError(
            "Concurrent-index lock timeout must be configured between transactions."
        )

    prior = (
        connection.execute(
            text(
                "SELECT current_setting('lock_timeout') AS display_value, "
                "setting::bigint AS milliseconds "
                "FROM pg_settings WHERE name = 'lock_timeout'"
            )
        )
        .mappings()
        .one()
    )
    connection.rollback()
    configured_ms = max(1, int(timeout_seconds * 1000))
    prior_ms = int(prior["milliseconds"] or 0)
    effective_ms = min(prior_ms, configured_ms) if prior_ms else configured_ms
    connection.execute(
        text("SELECT set_config('lock_timeout', :timeout, false)"),
        {"timeout": f"{effective_ms}ms"},
    )
    connection.commit()
    try:
        yield
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(
            text("SELECT set_config('lock_timeout', :timeout, false)"),
            {"timeout": str(prior["display_value"])},
        )
        connection.commit()


__all__ = [
    "POSTGRES_CONCURRENT_INDEX_CONTRACTS",
    "PostgresIndexContract",
    "drifted_postgres_indexes",
    "postgres_session_lock_timeout",
]
