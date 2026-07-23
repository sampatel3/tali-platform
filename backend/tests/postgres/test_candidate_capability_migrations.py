from __future__ import annotations

import importlib.util
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event

import sqlalchemy as sa
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration(filename: str, module_name: str):
    path = Path(__file__).parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_pre_185_schema(connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE organizations (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE roles (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            role_kind VARCHAR(16) NOT NULL DEFAULT 'standard',
            ats_owner_role_id INTEGER NULL,
            job_spec_text TEXT NULL,
            deleted_at TIMESTAMPTZ NULL,
            CONSTRAINT roles_ats_owner_role_id_fkey
                FOREIGN KEY (ats_owner_role_id) REFERENCES roles(id) ON DELETE CASCADE
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            cv_text TEXT NULL
        );
        CREATE TABLE candidate_applications (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            candidate_id INTEGER NOT NULL REFERENCES candidates(id),
            role_id INTEGER NOT NULL REFERENCES roles(id),
            deleted_at TIMESTAMPTZ NULL,
            cv_text TEXT NULL,
            pipeline_stage VARCHAR NOT NULL DEFAULT 'applied',
            pipeline_stage_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            pipeline_stage_source VARCHAR NOT NULL DEFAULT 'system',
            application_outcome VARCHAR NOT NULL DEFAULT 'open',
            application_outcome_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT uq_candidate_role_application UNIQUE (candidate_id, role_id)
        );
        CREATE TABLE share_links (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE sister_role_evaluations (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            source_application_id INTEGER NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            pipeline_stage VARCHAR(32) NOT NULL DEFAULT 'applied',
            pipeline_stage_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            pipeline_stage_source VARCHAR(16) NOT NULL DEFAULT 'system',
            spec_fingerprint VARCHAR(64) NOT NULL,
            cv_fingerprint VARCHAR(64) NULL,
            role_fit_score DOUBLE PRECISION NULL,
            summary TEXT NULL,
            details JSON NULL,
            history JSON NULL,
            model_version VARCHAR(100) NULL,
            prompt_version VARCHAR(100) NULL,
            trace_id VARCHAR(100) NULL,
            cache_hit BOOLEAN NOT NULL DEFAULT false,
            error_message TEXT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NULL,
            dispatch_attempted_at TIMESTAMPTZ NULL,
            last_error_code VARCHAR(100) NULL,
            queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ NULL,
            scored_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT sister_role_evaluations_source_application_id_fkey
                FOREIGN KEY (source_application_id)
                REFERENCES candidate_applications(id) ON DELETE CASCADE,
            CONSTRAINT uq_sister_evaluations_role_application
                UNIQUE (role_id, source_application_id)
        );
        CREATE TABLE agent_decisions (
            id BIGINT PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            role_id INTEGER NOT NULL REFERENCES roles(id),
            application_id INTEGER NOT NULL REFERENCES candidate_applications(id),
            status VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ NULL,
            resolution_note TEXT NULL,
            idempotency_key VARCHAR NOT NULL,
            CONSTRAINT uq_agent_decisions_idempotency_key UNIQUE (idempotency_key)
        );
        CREATE TABLE assessments (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NULL REFERENCES organizations(id),
            candidate_id INTEGER NULL REFERENCES candidates(id),
            role_id INTEGER NULL REFERENCES roles(id),
            application_id INTEGER NULL REFERENCES candidate_applications(id),
            is_voided BOOLEAN NOT NULL DEFAULT false
        );
        CREATE TABLE candidate_application_events (
            id INTEGER PRIMARY KEY,
            application_id INTEGER NOT NULL REFERENCES candidate_applications(id),
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            event_type VARCHAR NOT NULL,
            from_stage VARCHAR NULL,
            to_stage VARCHAR NULL,
            from_outcome VARCHAR NULL,
            to_outcome VARCHAR NULL,
            actor_type VARCHAR NOT NULL DEFAULT 'system',
            actor_id INTEGER NULL,
            reason TEXT NULL,
            metadata JSON NULL,
            idempotency_key VARCHAR NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_application_event_idempotency_key
                UNIQUE (application_id, idempotency_key)
        )
        """
    )
    # Revision 143 is production state before 185. Keeping this trigger in the
    # fixture prevents a false green from any migration that rewrites audit.
    connection.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION reject_candidate_application_event_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'candidate_application_events is append-only; UPDATE is not permitted (id=%%)',
                OLD.id
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_candidate_application_events_no_update
        BEFORE UPDATE ON candidate_application_events
        FOR EACH ROW
        EXECUTE FUNCTION reject_candidate_application_event_update()
        """
    )


def _seed_populated_legacy_state(connection) -> None:
    connection.exec_driver_sql(
        "INSERT INTO organizations (id) VALUES (1), (2)"
    )
    connection.exec_driver_sql(
        """
        INSERT INTO roles (
            id, organization_id, role_kind, ats_owner_role_id, job_spec_text
        ) VALUES
            (10, 1, 'standard', NULL, 'Owner role'),
            (20, 1, 'sister', 10, 'Independent related role')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO candidates (id, organization_id, cv_text) VALUES
            (100, 1, 'Candidate 100 CV'),
            (102, 1, 'Direct-only related candidate CV')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO candidate_applications (
            id, organization_id, candidate_id, role_id, cv_text,
            pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
            application_outcome, application_outcome_updated_at, created_at
        ) VALUES
            (1000, 1, 100, 10, 'Owner evidence',
             'applied', '2026-07-01T00:00:00Z', 'system',
             'open', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'),
            (1001, 1, 100, 20, 'Direct related evidence',
             'review', '2026-07-10T00:00:00Z', 'recruiter',
             'rejected', '2026-07-11T00:00:00Z', '2026-07-02T00:00:00Z'),
            (1003, 1, 102, 20, 'Direct-only related evidence',
             'applied', '2026-07-12T00:00:00Z', 'recruiter',
             'open', '2026-07-12T00:00:00Z', '2026-07-12T00:00:00Z')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO sister_role_evaluations (
            id, organization_id, role_id, source_application_id, status,
            pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
            spec_fingerprint, cv_fingerprint, role_fit_score, summary, history,
            model_version, prompt_version, trace_id, scored_at, created_at
        ) VALUES
            (2000, 1, 20, 1000, 'done',
             'applied', '2026-07-04T00:00:00Z', 'system',
             repeat('a', 64), repeat('b', 64), 88, 'Owner-backed score',
             '[{"older": true}]', 'model-a', 'prompt-a', 'trace-a',
             '2026-07-09T00:00:00Z', '2026-07-04T00:00:00Z'),
            (2001, 1, 20, 1001, 'done',
             'review', '2026-07-10T00:00:00Z', 'recruiter',
             repeat('a', 64), repeat('c', 64), 75, 'Direct score',
             NULL, 'model-b', 'prompt-b', 'trace-b',
             '2026-07-08T00:00:00Z', '2026-07-05T00:00:00Z'),
            (2003, 1, 20, 1003, 'done',
             'applied', '2026-07-12T00:00:00Z', 'recruiter',
             repeat('f', 64), repeat('0', 64), 81, 'Direct-only score',
             NULL, 'model-c', 'prompt-c', 'trace-c',
             '2026-07-12T00:00:00Z', '2026-07-12T00:00:00Z')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO agent_decisions (
            id, organization_id, role_id, application_id, status,
            created_at, idempotency_key
        ) VALUES
            (500, 1, 20, 1000, 'pending', '2026-07-10T01:00:00Z', 'decision-500'),
            (501, 1, 20, 1001, 'reverted_for_feedback',
             '2026-07-10T02:00:00Z', 'decision-501'),
            (502, 1, 20, 1000, 'processing',
             '2026-07-10T03:00:00Z', 'decision-502'),
            (503, 1, 20, 1001, 'pending',
             '2026-07-10T04:00:00Z', 'decision-503')
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO assessments (
            id, organization_id, candidate_id, role_id, application_id
        ) VALUES (600, 1, 100, 20, 1000)
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO candidate_application_events (
            id, application_id, organization_id, event_type, actor_type,
            metadata, idempotency_key, created_at
        ) VALUES
            (700, 1000, 1, 'agent_decision_queued', 'agent',
             '{"decision_id": 500}', 'queue-500', '2026-07-10T01:00:00Z'),
            (701, 1000, 1, 'pipeline_stage_changed', 'recruiter',
             '{"acting_role_id": 20, "decision_id": 500}',
             'move-500', '2026-07-10T04:00:00Z'),
            (702, 1000, 1, 'agent_decision_queued', 'agent',
             '{"acting_role_id": 10, "decision_id": 500}',
             'mismatched-500', '2026-07-10T05:00:00Z'),
            (707, 1000, 2, 'recruiter_note', 'recruiter',
             NULL, 'legacy-wrong-tenant', '2026-07-10T06:00:00Z')
        """
    )


def _create_pre_188_race_schema(connection) -> None:
    """Create only the populated schema contract revision 188 consumes."""

    connection.exec_driver_sql(
        """
        CREATE TABLE organizations (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE roles (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id)
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id)
        );
        CREATE TABLE candidate_applications (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            candidate_id INTEGER NOT NULL REFERENCES candidates(id),
            role_id INTEGER NOT NULL REFERENCES roles(id)
        );
        CREATE TABLE sister_role_evaluations (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            role_id INTEGER NOT NULL REFERENCES roles(id),
            candidate_id INTEGER NOT NULL REFERENCES candidates(id),
            source_application_id INTEGER NOT NULL
                REFERENCES candidate_applications(id),
            deleted_at TIMESTAMPTZ NULL
        );
        CREATE TABLE agent_decisions (
            id BIGINT PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            role_id INTEGER NOT NULL REFERENCES roles(id),
            application_id INTEGER NOT NULL
                REFERENCES candidate_applications(id),
            status VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ NULL,
            resolution_note TEXT NULL,
            idempotency_key VARCHAR NOT NULL UNIQUE
        );

        INSERT INTO organizations (id) VALUES (1);
        INSERT INTO roles (id, organization_id) VALUES (10, 1), (20, 1);
        INSERT INTO candidates (id, organization_id) VALUES (100, 1), (101, 1);
        INSERT INTO candidate_applications (
            id, organization_id, candidate_id, role_id
        ) VALUES
            (1000, 1, 100, 10),
            (1001, 1, 100, 10),
            (1010, 1, 101, 10),
            (1011, 1, 101, 10);
        INSERT INTO sister_role_evaluations (
            id, organization_id, role_id, candidate_id,
            source_application_id
        ) VALUES
            (2000, 1, 20, 100, 1000),
            (2001, 1, 20, 101, 1010);
        INSERT INTO agent_decisions (
            id, organization_id, role_id, application_id, status,
            idempotency_key
        ) VALUES
            (500, 1, 20, 1000, 'pending', 'existing-active'),
            (501, 1, 20, 1001, 'discarded', 'existing-inactive');
        """
    )


@contextmanager
def _connection_in_schema(engine, schema: str):
    """Use a test schema without leaking session search_path into the pool."""

    with engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.exec_driver_sql("RESET search_path")
            connection.commit()


def test_migration_188_guard_closes_pre_index_writer_race(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"decision_slot_race_{uuid.uuid4().hex}"
    migration_188 = _load_migration(
        "188_enforce_active_decision_slot.py",
        f"decision_slot_race_{uuid.uuid4().hex}",
    )
    repair_entered = Event()
    continue_repair = Event()
    first_inserted = Event()
    release_first = Event()
    second_attempting = Event()

    with postgres_search_engine.connect() as setup:
        setup.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        setup.commit()
    with _connection_in_schema(postgres_search_engine, schema) as setup:
        _create_pre_188_race_schema(setup)
        setup.commit()

    original_repair = migration_188._repair_active_decision_slots

    def pause_before_repair() -> None:
        repair_entered.set()
        if not continue_repair.wait(timeout=10):
            raise AssertionError("test did not release migration repair")
        original_repair()

    monkeypatch.setattr(
        migration_188,
        "_repair_active_decision_slots",
        pause_before_repair,
    )

    migration_connection = postgres_search_engine.connect()
    migration_connection.exec_driver_sql(f'SET search_path TO "{schema}"')
    migration_connection.commit()
    migration_context = MigrationContext.configure(migration_connection)
    operations = Operations(migration_context)
    monkeypatch.setattr(migration_188, "op", operations)

    def run_migration() -> None:
        with migration_context.begin_transaction():
            migration_188.upgrade()

    def insert_first_concurrent_writer() -> None:
        with _connection_in_schema(postgres_search_engine, schema) as writer:
            writer.exec_driver_sql(
                """
                INSERT INTO agent_decisions (
                    id, organization_id, role_id, application_id, status,
                    idempotency_key
                ) VALUES (
                    510, 1, 20, 1010, 'pending', 'concurrent-first'
                )
                """
            )
            first_inserted.set()
            if not release_first.wait(timeout=10):
                raise AssertionError("test did not release first writer")
            writer.commit()

    def insert_second_concurrent_writer() -> str:
        if not first_inserted.wait(timeout=10):
            raise AssertionError("first writer did not acquire its slot")
        with _connection_in_schema(postgres_search_engine, schema) as writer:
            try:
                second_attempting.set()
                writer.exec_driver_sql(
                    """
                    INSERT INTO agent_decisions (
                        id, organization_id, role_id, application_id, status,
                        idempotency_key
                    ) VALUES (
                        511, 1, 20, 1011, 'pending', 'concurrent-second'
                    )
                    """
                )
                writer.commit()
            except sa.exc.IntegrityError:
                writer.rollback()
                return "rejected"
        return "inserted"

    try:
        with ExitStack() as stack:
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=3))
            # Release database workers before the executor waits for them if a
            # regression assertion aborts the body early.
            stack.callback(continue_repair.set)
            stack.callback(release_first.set)
            migration_future = executor.submit(run_migration)
            assert repair_entered.wait(timeout=10)

            with _connection_in_schema(
                postgres_search_engine,
                schema,
            ) as observer:
                # The trigger and candidate column are visible because their
                # transaction committed before the repair was allowed to start.
                assert observer.exec_driver_sql(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'agent_decisions'
                      AND column_name = 'candidate_id'
                    """
                ).scalar_one() == 1
                assert observer.exec_driver_sql(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgrelid = 'agent_decisions'::regclass
                      AND tgname = 'trg_agent_decisions_resolve_candidate'
                      AND NOT tgisinternal
                    """
                ).scalar_one() == 1
                assert observer.exec_driver_sql(
                    """
                    SELECT to_regclass(
                        'uq_agent_decisions_active_org_role_candidate'
                    )
                    """
                ).scalar_one() is None

                # A pre-188 writer omits candidate_id. The trigger resolves the
                # source candidate and rejects both a new duplicate and an
                # inactive-to-active status transition before the index exists.
                for statement in (
                    """
                    INSERT INTO agent_decisions (
                        id, organization_id, role_id, application_id, status,
                        idempotency_key
                    ) VALUES (
                        502, 1, 20, 1001, 'pending', 'old-writer-duplicate'
                    )
                    """,
                    """
                    UPDATE agent_decisions
                    SET status = 'processing'
                    WHERE id = 501
                    """,
                ):
                    with pytest.raises(sa.exc.IntegrityError):
                        observer.exec_driver_sql(statement)
                    observer.rollback()

            first_future = executor.submit(insert_first_concurrent_writer)
            assert first_inserted.wait(timeout=10)
            second_future = executor.submit(insert_second_concurrent_writer)
            assert second_attempting.wait(timeout=10)
            with pytest.raises(FutureTimeoutError):
                second_future.result(timeout=0.25)
            release_first.set()
            first_future.result(timeout=10)
            assert second_future.result(timeout=10) == "rejected"

            with _connection_in_schema(
                postgres_search_engine,
                schema,
            ) as observer:
                assert observer.exec_driver_sql(
                    """
                    SELECT id, candidate_id
                    FROM agent_decisions
                    WHERE role_id = 20
                      AND status IN (
                            'pending',
                            'processing',
                            'reverted_for_feedback'
                      )
                    ORDER BY id
                    """
                ).all() == [(500, None), (510, 101)]

            continue_repair.set()
            migration_future.result(timeout=20)
    finally:
        release_first.set()
        continue_repair.set()
        migration_connection.rollback()
        migration_connection.exec_driver_sql("RESET search_path")
        migration_connection.commit()
        migration_connection.close()
        with postgres_search_engine.connect() as cleanup:
            cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cleanup.exec_driver_sql("RESET search_path")
            cleanup.commit()


def test_migration_185_retries_after_committed_additive_phase(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"membership_expand_retry_{uuid.uuid4().hex}"
    migration_185 = _load_migration(
        "185_related_role_membership.py",
        f"membership_expand_retry_{uuid.uuid4().hex}",
    )

    class AdditivePhaseCommitted(RuntimeError):
        pass

    with postgres_search_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        _create_pre_185_schema(connection)
        _seed_populated_legacy_state(connection)
        connection.commit()
        migration_context = MigrationContext.configure(connection)
        operations = Operations(migration_context)
        monkeypatch.setattr(migration_185, "op", operations)
        original_boundary = migration_185._commit_additive_schema_phase

        def stop_after_committed_expand() -> None:
            original_boundary()
            raise AdditivePhaseCommitted

        monkeypatch.setattr(
            migration_185,
            "_commit_additive_schema_phase",
            stop_after_committed_expand,
        )
        with pytest.raises(AdditivePhaseCommitted):
            with migration_context.begin_transaction():
                migration_185.upgrade()
        connection.rollback()

        # The additive schema and mixed-version guard are durable even though
        # the revision was not stamped. Re-entry must not fail on duplicate
        # columns, constraints, or triggers.
        assert connection.exec_driver_sql(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'sister_role_evaluations'
              AND column_name = 'candidate_id'
            """
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            """
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid = 'sister_role_evaluations'::regclass
              AND tgname = 'trg_sister_evaluations_resolve_candidate'
              AND NOT tgisinternal
            """
        ).scalar_one() == 1

        monkeypatch.setattr(
            migration_185,
            "_commit_additive_schema_phase",
            original_boundary,
        )
        with migration_context.begin_transaction():
            migration_185.upgrade()

        assert connection.exec_driver_sql(
            """
            SELECT count(*)
            FROM sister_role_evaluations
            WHERE role_id = 20
              AND candidate_id = 100
              AND deleted_at IS NULL
            """
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'sister_role_evaluations'
              AND column_name = 'candidate_id'
            """
        ).scalar_one() == "NO"

        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        connection.exec_driver_sql("RESET search_path")
        connection.commit()


def test_migration_185_promotes_pre_snapshot_legacy_shadow(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"membership_pre_snapshot_shadow_{uuid.uuid4().hex}"
    migration_185 = _load_migration(
        "185_related_role_membership.py",
        f"membership_pre_snapshot_shadow_{uuid.uuid4().hex}",
    )
    snapshot_starting = Event()
    continue_migration = Event()

    with postgres_search_engine.connect() as setup:
        setup.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        setup.commit()
    with _connection_in_schema(postgres_search_engine, schema) as setup:
        _create_pre_185_schema(setup)
        _seed_populated_legacy_state(setup)
        setup.exec_driver_sql(
            """
            INSERT INTO candidates (id, organization_id, cv_text)
            VALUES (101, 1, 'Concurrent legacy writer evidence');
            INSERT INTO candidate_applications (
                id, organization_id, candidate_id, role_id, cv_text,
                pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
                application_outcome, application_outcome_updated_at, created_at
            ) VALUES (
                1002, 1, 101, 10, 'Concurrent legacy writer evidence',
                'applied', now(), 'system', 'open', now(), now()
            )
            """
        )
        setup.commit()

    def pause_at_snapshot_boundary(phase: str) -> None:
        if phase != "before":
            return
        snapshot_starting.set()
        if not continue_migration.wait(timeout=10):
            raise AssertionError("test did not release legacy snapshot")

    monkeypatch.setattr(
        migration_185,
        "_legacy_implicit_snapshot_boundary",
        pause_at_snapshot_boundary,
    )

    migration_connection = postgres_search_engine.connect()
    migration_connection.exec_driver_sql(f'SET search_path TO "{schema}"')
    migration_connection.commit()
    migration_context = MigrationContext.configure(migration_connection)
    operations = Operations(migration_context)
    monkeypatch.setattr(migration_185, "op", operations)

    def run_migration() -> None:
        with migration_context.begin_transaction():
            migration_185.upgrade()

    try:
        with ExitStack() as stack:
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(continue_migration.set)
            migration_future = executor.submit(run_migration)
            assert snapshot_starting.wait(timeout=10)

            with _connection_in_schema(
                postgres_search_engine,
                schema,
            ) as legacy_writer:
                # This is the exact pre-185 insert shape. It races after the
                # compatibility trigger commits but before snapshot/dedupe.
                legacy_writer.exec_driver_sql(
                    """
                    INSERT INTO sister_role_evaluations (
                        id, organization_id, role_id, source_application_id,
                        status, pipeline_stage, pipeline_stage_updated_at,
                        pipeline_stage_source, spec_fingerprint, cv_fingerprint,
                        queued_at, created_at
                    ) VALUES (
                        2002, 1, 20, 1002, 'pending', 'applied', now(), 'system',
                        repeat('d', 64), repeat('e', 64), now(), now()
                    )
                    """
                )
                legacy_writer.commit()
                assert legacy_writer.exec_driver_sql(
                    """
                    SELECT candidate_id, status, membership_source,
                           deleted_at IS NOT NULL, last_error_code
                    FROM sister_role_evaluations
                    WHERE id = 2002
                    """
                ).one() == (
                    101,
                    "excluded",
                    "legacy_compat_shadow",
                    True,
                    "legacy_inferred_membership_ignored",
                )

            continue_migration.set()
            migration_future.result(timeout=20)

        with _connection_in_schema(
            postgres_search_engine,
            schema,
        ) as observer:
            assert observer.exec_driver_sql(
                """
                SELECT id, candidate_id, status, membership_source,
                       deleted_at IS NULL, error_message, last_error_code,
                       version
                FROM sister_role_evaluations
                WHERE id = 2002
                """
            ).one() == (
                2002,
                101,
                "stale_held",
                "legacy_implicit_snapshot",
                True,
                "Explicit re-evaluation is required after membership migration",
                None,
                2,
            )
            assert observer.exec_driver_sql(
                """
                SELECT count(*)
                FROM sister_role_evaluations
                WHERE role_id = 20
                  AND candidate_id = 101
                  AND deleted_at IS NULL
                """
            ).scalar_one() == 1
    finally:
        continue_migration.set()
        migration_connection.rollback()
        migration_connection.exec_driver_sql("RESET search_path")
        migration_connection.commit()
        migration_connection.close()
        with postgres_search_engine.connect() as cleanup:
            cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cleanup.exec_driver_sql("RESET search_path")
            cleanup.commit()


def test_migration_185_keeps_post_snapshot_legacy_shadow_archived(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"membership_post_snapshot_shadow_{uuid.uuid4().hex}"
    migration_185 = _load_migration(
        "185_related_role_membership.py",
        f"membership_post_snapshot_shadow_{uuid.uuid4().hex}",
    )
    snapshot_complete = Event()
    continue_migration = Event()

    with postgres_search_engine.connect() as setup:
        setup.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        setup.commit()
    with _connection_in_schema(postgres_search_engine, schema) as setup:
        _create_pre_185_schema(setup)
        _seed_populated_legacy_state(setup)
        setup.commit()

    def pause_at_snapshot_boundary(phase: str) -> None:
        if phase != "after":
            return
        snapshot_complete.set()
        if not continue_migration.wait(timeout=10):
            raise AssertionError("test did not release membership dedupe")

    monkeypatch.setattr(
        migration_185,
        "_legacy_implicit_snapshot_boundary",
        pause_at_snapshot_boundary,
    )

    migration_connection = postgres_search_engine.connect()
    migration_connection.exec_driver_sql(f'SET search_path TO "{schema}"')
    migration_connection.commit()
    migration_context = MigrationContext.configure(migration_connection)
    operations = Operations(migration_context)
    monkeypatch.setattr(migration_185, "op", operations)

    def run_migration() -> None:
        with migration_context.begin_transaction():
            migration_185.upgrade()

    try:
        with ExitStack() as stack:
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(continue_migration.set)
            migration_future = executor.submit(run_migration)
            assert snapshot_complete.wait(timeout=10)

            with _connection_in_schema(
                postgres_search_engine,
                schema,
            ) as legacy_writer:
                # This owner application and inferred fan-out both arrive
                # after the one-time pool snapshot has completed.
                legacy_writer.exec_driver_sql(
                    """
                    INSERT INTO candidates (id, organization_id, cv_text)
                    VALUES (101, 1, 'Post-snapshot legacy writer evidence');
                    INSERT INTO candidate_applications (
                        id, organization_id, candidate_id, role_id, cv_text,
                        pipeline_stage, pipeline_stage_updated_at,
                        pipeline_stage_source, application_outcome,
                        application_outcome_updated_at, created_at
                    ) VALUES (
                        1002, 1, 101, 10,
                        'Post-snapshot legacy writer evidence',
                        'applied', now(), 'system', 'open', now(), now()
                    );
                    INSERT INTO sister_role_evaluations (
                        id, organization_id, role_id, source_application_id,
                        status, pipeline_stage, pipeline_stage_updated_at,
                        pipeline_stage_source, spec_fingerprint, cv_fingerprint,
                        queued_at, created_at
                    ) VALUES (
                        2002, 1, 20, 1002, 'pending', 'applied', now(), 'system',
                        repeat('d', 64), repeat('e', 64), now(), now()
                    )
                    """
                )
                legacy_writer.commit()
                assert legacy_writer.exec_driver_sql(
                    """
                    SELECT candidate_id, status, membership_source,
                           deleted_at IS NOT NULL, last_error_code
                    FROM sister_role_evaluations
                    WHERE id = 2002
                    """
                ).one() == (
                    101,
                    "excluded",
                    "legacy_compat_shadow",
                    True,
                    "legacy_inferred_membership_ignored",
                )

            continue_migration.set()
            migration_future.result(timeout=20)

        with _connection_in_schema(
            postgres_search_engine,
            schema,
        ) as observer:
            assert observer.exec_driver_sql(
                """
                SELECT candidate_id, status, membership_source,
                       deleted_at IS NOT NULL, last_error_code, version
                FROM sister_role_evaluations
                WHERE id = 2002
                """
            ).one() == (
                101,
                "excluded",
                "legacy_compat_shadow",
                True,
                "legacy_inferred_membership_ignored",
                1,
            )
            assert observer.exec_driver_sql(
                """
                SELECT count(*)
                FROM sister_role_evaluations
                WHERE role_id = 20
                  AND candidate_id = 101
                  AND deleted_at IS NULL
                """
            ).scalar_one() == 0
    finally:
        continue_migration.set()
        migration_connection.rollback()
        migration_connection.exec_driver_sql("RESET search_path")
        migration_connection.commit()
        migration_connection.close()
        with postgres_search_engine.connect() as cleanup:
            cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cleanup.exec_driver_sql("RESET search_path")
            cleanup.commit()


def test_migration_186_allows_only_archived_membership_assessment_holds(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"candidate_action_hold_{uuid.uuid4().hex}"
    migration_185 = _load_migration(
        "185_related_role_membership.py",
        f"related_role_membership_{uuid.uuid4().hex}",
    )
    migration_186 = _load_migration(
        "186_candidate_action_provenance.py",
        f"candidate_action_provenance_{uuid.uuid4().hex}",
    )

    with postgres_search_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        _create_pre_185_schema(connection)
        _seed_populated_legacy_state(connection)
        connection.commit()
        migration_context = MigrationContext.configure(connection)
        operations = Operations(migration_context)
        monkeypatch.setattr(migration_185, "op", operations)
        monkeypatch.setattr(migration_186, "op", operations)

        with migration_context.begin_transaction():
            migration_185.upgrade()
        with migration_context.begin_transaction():
            migration_186.upgrade()

        # Candidate 100's assessment is linked to the ATS-owner application,
        # while its independent related-role membership is stored separately.
        # A late receipt can arrive after that membership is removed; the
        # receipt must remain appendable without restoring role-local state.
        connection.exec_driver_sql(
            """
            UPDATE sister_role_evaluations
            SET deleted_at = now(), version = version + 1
            WHERE role_id = 20 AND candidate_id = 100
            """
        )
        state_before = connection.exec_driver_sql(
            """
            SELECT pipeline_stage, application_outcome, version,
                   deleted_at IS NOT NULL
            FROM sister_role_evaluations
            WHERE id = 2001
            """
        ).one()

        for event_id, event_type in (
            (714, "role_pipeline_stage_transition_held"),
            (715, "assessment_invite_sent"),
            (716, "assessment_invite_resent"),
            (717, "assessment_retake_sent"),
            (718, "assessment_invite_pipeline_transition_held"),
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO candidate_application_events (
                    id, application_id, organization_id, role_id, event_type,
                    actor_type, metadata, target_stage, effect_status,
                    idempotency_key, created_at
                ) VALUES (
                    %(event_id)s, 1000, 1, 20, %(event_type)s,
                    'system',
                    '{"assessment_id": 600, "acting_role_id": 20}',
                    'invited', 'held', %(idempotency_key)s, now()
                )
                """,
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "idempotency_key": f"archived-membership-hold-{event_id}",
                },
            )

        assert connection.exec_driver_sql(
            """
            SELECT id, role_id, event_type, effect_status, target_stage
            FROM candidate_application_events
            WHERE id IN (714, 715, 716, 717, 718)
            ORDER BY id
            """
        ).all() == [
            (
                714,
                20,
                "role_pipeline_stage_transition_held",
                "held",
                "invited",
            ),
            (715, 20, "assessment_invite_sent", "held", "invited"),
            (716, 20, "assessment_invite_resent", "held", "invited"),
            (717, 20, "assessment_retake_sent", "held", "invited"),
            (
                718,
                20,
                "assessment_invite_pipeline_transition_held",
                "held",
                "invited",
            ),
        ]
        assert connection.exec_driver_sql(
            """
            SELECT pipeline_stage, application_outcome, version,
                   deleted_at IS NOT NULL
            FROM sister_role_evaluations
            WHERE id = 2001
            """
        ).one() == state_before

        # Historical authority is audit-only: a caller cannot use it for a
        # transition, omit matching assessment provenance, or claim success.
        rejected_events = (
            (719, "role_pipeline_stage_changed", "held", 600, None),
            (
                720,
                "role_pipeline_stage_transition_held",
                "held",
                999999,
                None,
            ),
            (
                721,
                "role_pipeline_stage_transition_held",
                "held",
                600,
                "review",
            ),
            (
                722,
                "role_pipeline_stage_transition_held",
                "confirmed",
                600,
                None,
            ),
        )
        for event_id, event_type, effect_status, assessment_id, to_stage in (
            rejected_events
        ):
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        """
                        INSERT INTO candidate_application_events (
                            id, application_id, organization_id, role_id,
                            event_type, actor_type, metadata, to_stage,
                            target_stage, effect_status, idempotency_key,
                            created_at
                        ) VALUES (
                            %(event_id)s, 1000, 1, 20, %(event_type)s,
                            'system',
                            json_build_object(
                                'assessment_id', %(assessment_id)s,
                                'acting_role_id', 20
                            ),
                            %(to_stage)s, 'invited', %(effect_status)s,
                            %(idempotency_key)s, now()
                        )
                        """,
                        {
                            "event_id": event_id,
                            "event_type": event_type,
                            "effect_status": effect_status,
                            "assessment_id": assessment_id,
                            "to_stage": to_stage,
                            "idempotency_key": (
                                f"rejected-archived-membership-{event_id}"
                            ),
                        },
                    )

        connection.rollback()
        connection.exec_driver_sql("RESET search_path")
        connection.commit()
        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.commit()


def test_populated_role_membership_and_action_migrations_are_rolling_safe(
    postgres_search_engine,
    monkeypatch,
):
    schema = f"candidate_capability_migration_{uuid.uuid4().hex}"
    migration_185 = _load_migration(
        "185_related_role_membership.py",
        f"related_role_membership_{uuid.uuid4().hex}",
    )
    migration_186 = _load_migration(
        "186_candidate_action_provenance.py",
        f"candidate_action_provenance_{uuid.uuid4().hex}",
    )
    migration_187 = _load_migration(
        "187_candidate_capability_indexes.py",
        f"candidate_capability_indexes_{uuid.uuid4().hex}",
    )
    migration_188 = _load_migration(
        "188_enforce_active_decision_slot.py",
        f"enforce_active_decision_slot_{uuid.uuid4().hex}",
    )

    with postgres_search_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        _create_pre_185_schema(connection)
        _seed_populated_legacy_state(connection)
        connection.commit()
        migration_context = MigrationContext.configure(connection)
        operations = Operations(migration_context)
        monkeypatch.setattr(migration_185, "op", operations)
        monkeypatch.setattr(migration_186, "op", operations)
        monkeypatch.setattr(migration_187, "op", operations)
        monkeypatch.setattr(migration_188, "op", operations)

        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE candidate_application_events SET reason = 'tampered' "
                    "WHERE id = 700"
                )

        with migration_context.begin_transaction():
            migration_185.upgrade()

        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO roles (
                        id, organization_id, role_kind, ats_owner_role_id,
                        job_spec_text
                    ) VALUES (31, 1, 'standard', 10, 'Invalid split identity')
                    """
                )

        membership = connection.exec_driver_sql(
            """
            SELECT id, candidate_id, source_application_id, ats_application_id,
                   pipeline_stage, pipeline_stage_source, application_outcome,
                   role_fit_score, history
            FROM sister_role_evaluations
            WHERE role_id = 20 AND candidate_id = 100 AND deleted_at IS NULL
            """
        ).mappings().one()
        assert membership["id"] == 2001
        assert membership["source_application_id"] == 1001
        assert membership["ats_application_id"] == 1000
        assert membership["pipeline_stage"] == "review"
        assert membership["pipeline_stage_source"] == "recruiter"
        assert membership["application_outcome"] == "rejected"
        assert membership["role_fit_score"] == 75
        assert membership["history"] is None
        assert connection.exec_driver_sql(
            "SELECT deleted_at IS NOT NULL FROM sister_role_evaluations WHERE id = 2000"
        ).scalar_one() is True
        assert connection.exec_driver_sql(
            "SELECT array_agg(application_id ORDER BY id) FROM agent_decisions"
        ).scalar_one() == [1000, 1001, 1000, 1001]
        assert connection.exec_driver_sql(
            "SELECT application_id FROM assessments WHERE id = 600"
        ).scalar_one() == 1000

        # Simulate the destructive part of the pre-185 sync loop. Membership is
        # now explicit, so an old process may no longer hard-delete it merely
        # because its inferred owner roster changed. The compatibility trigger
        # makes the old DELETE a no-op while preserving parent-table cascades.
        deleted = connection.exec_driver_sql(
            "DELETE FROM sister_role_evaluations WHERE id = 2003"
        )
        assert deleted.rowcount == 0
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sister_role_evaluations WHERE id = 2003"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT deleted_at IS NULL FROM sister_role_evaluations WHERE id = 2001"
        ).scalar_one() is True

        # This is the exact INSERT shape emitted by a pre-185 worker. It means
        # "fan this new ATS-owner application into every related role", which
        # would expand a role after its one-time snapshot. The compatibility
        # trigger must keep that old worker healthy while immediately archiving
        # the row outside the logical pool and scoring queue.
        connection.exec_driver_sql(
            """
            INSERT INTO candidates (id, organization_id, cv_text)
            VALUES (101, 1, 'Legacy writer evidence');
            INSERT INTO candidate_applications (
                id, organization_id, candidate_id, role_id, cv_text,
                pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
                application_outcome, application_outcome_updated_at, created_at
            ) VALUES (
                1002, 1, 101, 10, 'Legacy writer evidence',
                'applied', now(), 'system', 'open', now(), now()
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO sister_role_evaluations (
                id, organization_id, role_id, source_application_id,
                status, pipeline_stage, pipeline_stage_updated_at,
                pipeline_stage_source, spec_fingerprint, cv_fingerprint,
                queued_at, created_at
            ) VALUES (
                2002, 1, 20, 1002, 'pending', 'applied', now(), 'system',
                repeat('d', 64), repeat('e', 64), now(), now()
            )
            """
        )
        assert connection.exec_driver_sql(
            """
            SELECT candidate_id, status, membership_source,
                   deleted_at IS NOT NULL, last_error_code
            FROM sister_role_evaluations
            WHERE id = 2002
            """
        ).one() == (
            101,
            "excluded",
            "legacy_compat_shadow",
            True,
            "legacy_inferred_membership_ignored",
        )

        # A current writer makes membership identity explicit. That path is
        # allowed to restore the archived compatibility row, remains role-local,
        # and is protected against stale owner projections during the rollout.
        connection.exec_driver_sql(
            """
            UPDATE sister_role_evaluations
            SET candidate_id = 101,
                deleted_at = NULL,
                membership_source = 'direct',
                status = 'pending',
                error_message = NULL,
                last_error_code = NULL,
                version = version + 1
            WHERE id = 2002
            """
        )
        assert connection.exec_driver_sql(
            "SELECT candidate_id FROM sister_role_evaluations WHERE id = 2002"
        ).scalar_one() == 101
        # ATS transport identity is stricter than the generic application FK:
        # same tenant, same candidate, and the role's declared ATS owner must
        # all match. Wrong candidate, wrong owner, and wrong tenant fail closed.
        for invalid_application_id in (1002, 1001):
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "UPDATE sister_role_evaluations "
                        "SET ats_application_id = %s WHERE id = 2001"
                        % invalid_application_id
                    )
        connection.exec_driver_sql(
            """
            INSERT INTO roles (
                id, organization_id, role_kind, job_spec_text
            ) VALUES (50, 2, 'standard', 'Other tenant owner');
            INSERT INTO candidates (id, organization_id, cv_text)
            VALUES (150, 2, 'Other tenant candidate');
            INSERT INTO candidate_applications (
                id, organization_id, candidate_id, role_id, cv_text,
                pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
                application_outcome, application_outcome_updated_at, created_at
            ) VALUES (
                1050, 2, 150, 50, 'Other tenant application',
                'applied', now(), 'system', 'open', now(), now()
            )
            """
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE sister_role_evaluations "
                    "SET ats_application_id = 1050 WHERE id = 2001"
                )
        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations "
            "SET ats_application_id = 1002 WHERE id = 2002"
        )
        assert connection.exec_driver_sql(
            "SELECT ats_application_id FROM sister_role_evaluations WHERE id = 2002"
        ).scalar_one() == 1002
        # A pre-185 writer does not advance the membership version, so an owner
        # projection cannot overwrite role-local stage. Current writers change
        # state and version together.
        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations SET pipeline_stage = 'advanced' "
            "WHERE id = 2002"
        )
        assert connection.exec_driver_sql(
            "SELECT pipeline_stage FROM sister_role_evaluations WHERE id = 2002"
        ).scalar_one() == "applied"
        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations "
            "SET pipeline_stage = 'review', version = version + 1 WHERE id = 2002"
        )
        assert connection.exec_driver_sql(
            "SELECT pipeline_stage FROM sister_role_evaluations WHERE id = 2002"
        ).scalar_one() == "review"
        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations SET status = 'pending', "
            "next_attempt_at = now() + interval '1 hour', "
            "dispatch_attempted_at = now(), started_at = now(), "
            "version = version + 1 WHERE id = 2002"
        )
        workflow_before_close = connection.exec_driver_sql(
            "SELECT status, error_message, last_error_code, next_attempt_at, "
            "dispatch_attempted_at, started_at "
            "FROM sister_role_evaluations WHERE id = 2002"
        ).one()
        # A pre-185 owner reconcile treats shared ATS closure as role-local
        # exclusion. During a rolling deploy that exact legacy transition must
        # be suppressed without disturbing the independent scoring workflow.
        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations "
            "SET status = 'excluded', "
            "error_message = 'Shared ATS application is disqualified or closed', "
            "last_error_code = 'shared_application_closed', "
            "next_attempt_at = NULL, dispatch_attempted_at = NULL, "
            "started_at = NULL WHERE id = 2002"
        )
        assert connection.exec_driver_sql(
            "SELECT status, error_message, last_error_code, next_attempt_at, "
            "dispatch_attempted_at, started_at "
            "FROM sister_role_evaluations WHERE id = 2002"
        ).one() == workflow_before_close

        # Compatibility protection must not defeat genuine parent cascades.
        connection.exec_driver_sql(
            """
            INSERT INTO roles (
                id, organization_id, role_kind, ats_owner_role_id, job_spec_text
            ) VALUES (30, 1, 'sister', 10, 'Temporary related role');
            INSERT INTO sister_role_evaluations (
                id, organization_id, role_id, candidate_id,
                source_application_id, status, pipeline_stage,
                pipeline_stage_updated_at, pipeline_stage_source,
                spec_fingerprint, cv_fingerprint, queued_at, created_at
            ) VALUES (
                2004, 1, 30, 100, 1000, 'pending', 'applied', now(), 'system',
                repeat('1', 64), repeat('2', 64), now(), now()
            )
            """
        )
        connection.exec_driver_sql("DELETE FROM roles WHERE id = 30")
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sister_role_evaluations WHERE id = 2004"
        ).scalar_one() == 0
        candidate_column = next(
            column
            for column in sa.inspect(connection).get_columns(
                "sister_role_evaluations"
            )
            if column["name"] == "candidate_id"
        )
        assert candidate_column["nullable"] is False
        assert "uq_sister_evaluations_role_candidate" not in {
            constraint["name"]
            for constraint in sa.inspect(connection).get_unique_constraints(
                "sister_role_evaluations"
            )
        }
        # The rolling compatibility trigger closes the concurrency window
        # before revision 187 builds the partial unique index. A historical
        # shadow cannot be restored while another live membership exists.
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE sister_role_evaluations "
                    "SET deleted_at = NULL, version = version + 1 WHERE id = 2000"
                )

        connection.commit()
        with migration_context.begin_transaction():
            migration_186.upgrade()

        event_rows = connection.exec_driver_sql(
            """
            SELECT id, role_id, agent_decision_id
            FROM candidate_application_events
            WHERE id IN (700, 701, 702)
            ORDER BY id
            """
        ).all()
        # Revision 143 made these rows append-only. Migration 186 adds nullable
        # projection columns but never rewrites historical evidence.
        assert event_rows == [(700, None, None), (701, None, None), (702, None, None)]
        assert connection.exec_driver_sql(
            """
            SELECT organization_id, role_id
            FROM candidate_application_events
            WHERE id = 707
            """
        ).one() == (2, None)
        live_decisions = connection.exec_driver_sql(
            """
            SELECT id, status
            FROM agent_decisions
            WHERE status IN ('pending', 'processing', 'reverted_for_feedback')
            ORDER BY id
            """
        ).all()
        assert live_decisions == [
            (500, "pending"),
            (501, "reverted_for_feedback"),
            (502, "processing"),
            (503, "pending"),
        ]
        assert "uq_agent_decisions_active_org_role_candidate" not in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("agent_decisions")
        }

        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE candidate_application_events SET reason = 'tampered' "
                    "WHERE id = 700"
                )

        # A pre-186 decision event omits both normalized provenance columns.
        # Decision role wins even though its canonical source application was
        # changed by 185 and this historical event still names the owner app.
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, event_type, actor_type,
                metadata, idempotency_key, created_at
            ) VALUES (
                703, 1000, 1, 'agent_decision_queued', 'agent',
                '{"decision_id": 502}', 'legacy-after-upgrade', now()
            )
            """
        )
        assert connection.exec_driver_sql(
            """
            SELECT role_id, agent_decision_id
            FROM candidate_application_events WHERE id = 703
            """
        ).one() == (20, 502)

        connection.exec_driver_sql(
            "UPDATE sister_role_evaluations SET deleted_at = now() WHERE id = 2002"
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO candidate_application_events (
                        id, application_id, organization_id, role_id,
                        event_type, actor_type, created_at
                    ) VALUES (
                        711, 1002, 1, 20,
                        'pipeline_stage_changed', 'agent', now()
                    )
                    """
                )

        # Explicit normalized provenance is fail-closed. Invalid legacy JSON is
        # still best-effort, but a current writer may not silently lose the FK.
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO candidate_application_events (
                        id, application_id, organization_id, role_id,
                        agent_decision_id, event_type, actor_type, created_at
                    ) VALUES (
                        708, 1000, 1, 20, 999999,
                        'pipeline_stage_changed', 'agent', now()
                    )
                    """
                )

        connection.exec_driver_sql(
            """
            INSERT INTO roles (
                id, organization_id, role_kind, job_spec_text
            ) VALUES (40, 1, 'standard', 'Unrelated same-tenant role')
            """
        )
        # An untrusted legacy metadata hint degrades to the physical role.
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, event_type, actor_type,
                metadata, idempotency_key, created_at
            ) VALUES (
                709, 1000, 1, 'recruiter_note', 'recruiter',
                '{"acting_role_id": 40}', 'unauthorized-legacy-role', now()
            )
            """
        )
        assert connection.exec_driver_sql(
            "SELECT role_id FROM candidate_application_events WHERE id = 709"
        ).scalar_one() == 10
        # A current writer's normalized role is a fail-closed authorization
        # boundary, even when the unrelated role is in the same tenant.
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO candidate_application_events (
                        id, application_id, organization_id, role_id,
                        event_type, actor_type, created_at
                    ) VALUES (
                        710, 1000, 1, 40,
                        'pipeline_stage_changed', 'agent', now()
                    )
                    """
                )

        # Ordinary legacy events with no decision/acting-role evidence retain
        # the physical application's role.
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, event_type, actor_type,
                idempotency_key, created_at
            ) VALUES (704, 1000, 1, 'recruiter_note', 'recruiter',
                      'legacy-note', now())
            """
        )
        assert connection.exec_driver_sql(
            "SELECT role_id FROM candidate_application_events WHERE id = 704"
        ).scalar_one() == 10

        # Widening idempotency is deliberately deferred during the rolling
        # expand. The legacy constraint is a safe restriction and avoids any
        # uniqueness gap or mutation of immutable client keys.
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, role_id, event_type,
                actor_type, idempotency_key, created_at
            ) VALUES (705, 1000, 1, 10, 'pipeline_stage_changed', 'system',
                      'shared-client-key', now())
            """
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO candidate_application_events (
                        id, application_id, organization_id, role_id, event_type,
                        actor_type, idempotency_key, created_at
                    ) VALUES (
                        706, 1000, 1, 20, 'role_pipeline_stage_changed',
                        'system', 'shared-client-key', now()
                    )
                    """
                )

        # A requested provider target in metadata is not proof of a provider
        # movement. The logical transition and ATS movement retain independent
        # targets so an exact "advanced to Technical Interview" query cannot
        # be certified by the preceding Tali hand-off.
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, role_id, event_type,
                actor_type, to_stage, metadata, idempotency_key, created_at
            ) VALUES (
                712, 1000, 1, 10, 'pipeline_stage_changed', 'system',
                'advanced', '{"workable_target_stage": "Technical Interview"}',
                'logical-target-separation', now()
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO candidate_application_events (
                id, application_id, organization_id, role_id, event_type,
                actor_type, target_stage, idempotency_key, created_at
            ) VALUES (
                713, 1000, 1, 10, 'workable_moved', 'system',
                'Technical Interview', 'provider-target-confirmed', now()
            )
            """
        )
        assert connection.exec_driver_sql(
            """
            SELECT id, target_stage
            FROM candidate_application_events
            WHERE id IN (712, 713)
            ORDER BY id
            """
        ).all() == [(712, "advanced"), (713, "Technical Interview")]

        connection.commit()
        # Reproduce PostgreSQL's interrupted concurrent-build state. A failed
        # unique build leaves a same-name INVALID index behind, and plain
        # ``IF NOT EXISTS`` would silently skip it on the next deploy.
        connection.exec_driver_sql(
            "ALTER TABLE sister_role_evaluations "
            f"DISABLE TRIGGER {migration_185._CANDIDATE_TRIGGER}"
        )
        try:
            connection.exec_driver_sql(
                "UPDATE sister_role_evaluations SET deleted_at = NULL "
                "WHERE id = 2000"
            )
            connection.commit()
            with pytest.raises(sa.exc.IntegrityError):
                migration_187._run_concurrently(
                    "CREATE UNIQUE INDEX CONCURRENTLY "
                    f"{migration_187._LIVE_MEMBERSHIP_INDEX} "
                    "ON sister_role_evaluations (role_id, candidate_id) "
                    "WHERE deleted_at IS NULL"
                )
            assert connection.exec_driver_sql(
                """
                SELECT index_state.indisvalid, index_state.indisready
                FROM pg_index AS index_state
                WHERE index_state.indexrelid = to_regclass(
                    'uq_sister_evaluations_live_role_candidate'
                )
                """
            ).one() != (True, True)
        finally:
            connection.rollback()
            connection.exec_driver_sql(
                "UPDATE sister_role_evaluations SET deleted_at = now() "
                "WHERE id = 2000"
            )
            connection.exec_driver_sql(
                "ALTER TABLE sister_role_evaluations "
                f"ENABLE TRIGGER {migration_185._CANDIDATE_TRIGGER}"
            )
            connection.commit()

        with migration_context.begin_transaction():
            migration_187.upgrade()

        event_indexes = {
            index["name"]
            for index in sa.inspect(connection).get_indexes(
                "candidate_application_events"
            )
        }
        assert "ix_application_events_org_role_created" in event_indexes
        assert "ix_candidate_application_events_role_id" in event_indexes
        assert "ix_candidate_application_events_agent_decision_id" in event_indexes

        # The contraction is logical role/candidate scoped, not physical-
        # application or decision-type scoped. The live membership source wins
        # before status priority, so an obsolete owner-transport card cannot
        # block the candidate's direct related-role lifecycle.
        # Simulate an interrupted/older deployment that left a valid but weaker
        # same-named index. Migration 188 must inspect its contract and replace
        # it, not mistake name + indisvalid for sufficient protection.
        connection.exec_driver_sql(
            """
            CREATE UNIQUE INDEX uq_agent_decisions_active_org_role_candidate
            ON agent_decisions (role_id, application_id)
            WHERE status = 'pending'
            """
        )
        with migration_context.begin_transaction():
            migration_188.upgrade()
        # A concurrent index build can commit before Alembic stamps its
        # revision. Re-entering the migration must recognize the valid index
        # instead of dropping or rebuilding the live invariant.
        with migration_context.begin_transaction():
            migration_188.upgrade()
        assert connection.exec_driver_sql(
            "SELECT id, candidate_id, status FROM agent_decisions ORDER BY id"
        ).all() == [
            (500, 100, "discarded"),
            (501, 100, "reverted_for_feedback"),
            (502, 100, "discarded"),
            (503, 100, "discarded"),
        ]
        assert "uq_agent_decisions_active_org_role_candidate" in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("agent_decisions")
        }
        active_slot_index = next(
            index
            for index in sa.inspect(connection).get_indexes("agent_decisions")
            if index["name"] == "uq_agent_decisions_active_org_role_candidate"
        )
        assert active_slot_index["unique"] is True
        assert active_slot_index["column_names"] == [
            "organization_id",
            "role_id",
            "candidate_id",
        ]
        # The same candidate may hold an independent card in the ATS-owner role.
        # A second related-role card through the owner transport must conflict
        # with the surviving card on the direct related-role application.
        connection.exec_driver_sql(
            """
            INSERT INTO agent_decisions (
                id, organization_id, role_id, application_id, status,
                idempotency_key
            ) VALUES (
                505, 1, 10, 1000, 'pending', 'decision-505'
            )
            """
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO agent_decisions (
                        id, organization_id, role_id, application_id, status,
                        idempotency_key
                    ) VALUES (
                        504, 1, 20, 1000, 'pending', 'decision-504'
                    )
                    """
                )
        assert connection.exec_driver_sql(
            "SELECT candidate_id FROM agent_decisions WHERE id = 505"
        ).scalar_one() == 100
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    """
                    INSERT INTO agent_decisions (
                        id, organization_id, role_id, application_id,
                        candidate_id, status, idempotency_key
                    ) VALUES (
                        506, 1, 10, 1000, 102, 'pending', 'decision-506'
                    )
                    """
                )
        membership_indexes = {
            index["name"]: index
            for index in sa.inspect(connection).get_indexes(
                "sister_role_evaluations"
            )
        }
        live_membership_index = membership_indexes[
            "uq_sister_evaluations_live_role_candidate"
        ]
        assert live_membership_index["unique"] is True
        assert live_membership_index["column_names"] == ["role_id", "candidate_id"]
        assert "deleted_at IS NULL" in str(
            live_membership_index.get("dialect_options", {}).get(
                "postgresql_where", ""
            )
        )

        # Changing/removing transport ownership cannot leave stale typed links.
        # The membership and local state remain; only unsafe transport clears.
        connection.exec_driver_sql(
            "UPDATE roles SET ats_owner_role_id = NULL WHERE id = 20"
        )
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sister_role_evaluations "
            "WHERE role_id = 20 AND ats_application_id IS NOT NULL"
        ).scalar_one() == 0
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE sister_role_evaluations "
                    "SET ats_application_id = 1000 WHERE id = 2001"
                )

        # Expanded role/action truth cannot be represented by the prior schema.
        # Operational rollback runs old code against this forward-compatible
        # schema; destructive Alembic downgrade fails closed.
        with pytest.raises(RuntimeError, match="append-only"):
            migration_186.downgrade()
        with pytest.raises(RuntimeError, match="independent lifecycle"):
            migration_185.downgrade()
        with pytest.raises(RuntimeError, match="candidate identity"):
            migration_188.downgrade()
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM candidate_application_events WHERE id = 705"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT array_agg(status ORDER BY id) FROM agent_decisions"
        ).scalar_one() == [
            "discarded",
            "reverted_for_feedback",
            "discarded",
            "discarded",
            "pending",
        ]

        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        # ``search_path`` is session state, not transaction state. SQLAlchemy
        # may return this connection to the shared integration-test pool, so
        # leave it on the database default before the next PostgreSQL truth
        # test checks the real public schema and Alembic head.
        connection.exec_driver_sql("RESET search_path")
        connection.commit()
