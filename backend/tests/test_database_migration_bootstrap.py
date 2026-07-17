from __future__ import annotations

from datetime import datetime, timezone
import json
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
    _alembic_config,
    _preflight_database,
    _require_supported_database_dialect,
    main,
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
    assert script.get_heads() == ["189_shared_family_reject_repair"]


def test_preflight_allows_a_genuinely_empty_schema():
    engine = create_engine("sqlite://", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert _preflight_database(connection, _script_directory()) == "empty"
    finally:
        engine.dispose()


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
            ).scalar_one() == "189_shared_family_reject_repair"

            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                ).scalars()
            )
            assert POSTGRES_REQUIRED_INDEXES <= indexes
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
                for column in inspect(connection).get_columns(
                    "graph_ingest_dispatches"
                )
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
            assert connection.execute(
                text(
                    "SELECT count(*) FROM pg_proc WHERE proname = "
                    "'prevent_graph_ingest_manifest_mutation_v187'"
                )
            ).scalar_one() == 1

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
                assert observer.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "181_merge_workspace_bulk_role_pause"
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
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "189_shared_family_reject_repair"
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
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "173_related_role_drafts"

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
                ).mappings().one()
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
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "180_merge_related_role_workflow"
            assert connection.execute(
                text(
                    "SELECT agent_workspace_paused_at FROM organizations "
                    "WHERE id = 1001"
                )
            ).scalar_one() == paused_at
            assert connection.execute(
                text("SELECT agent_paused_at FROM roles WHERE id = 2001")
            ).scalar_one() is None
            assert dict(
                connection.execute(
                    text(
                        f"SELECT {evaluation_columns} "
                        "FROM sister_role_evaluations WHERE id = 5001"
                    )
                ).mappings().one()
            ) == legacy_evaluation

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
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "189_shared_family_reject_repair"

            preserved_evaluation = dict(
                connection.execute(
                    text(
                        f"SELECT {evaluation_columns} "
                        "FROM sister_role_evaluations WHERE id = 5001"
                    )
                ).mappings().one()
            )
            assert preserved_evaluation == legacy_evaluation

            workflow_state = connection.execute(
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
            ).mappings().one()
            assert workflow_state["pipeline_stage"] == "applied"
            assert workflow_state["pipeline_stage_updated_at"] is not None
            assert workflow_state["pipeline_stage_source"] == "system"

            related_indexes = {
                index["name"]: index
                for index in inspect(connection).get_indexes(
                    "sister_role_evaluations"
                )
            }
            workflow_index = related_indexes[
                "ix_sister_evaluations_role_pipeline_stage"
            ]
            assert workflow_index["column_names"] == [
                "role_id",
                "pipeline_stage",
            ]
            assert workflow_index["unique"] is False

            organization = connection.execute(
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
            ).mappings().one()
            assert organization == {
                "agent_workspace_paused_at": None,
                "agent_workspace_paused_reason": None,
                "agent_workspace_paused_by_user_id": None,
                "agent_workspace_paused_by_name": None,
                "agent_workspace_control_version": 8,
            }

            owner_role = connection.execute(
                text(
                    """
                    SELECT agent_paused_at, agent_paused_reason, version
                    FROM roles
                    WHERE id = 2001
                    """
                )
            ).mappings().one()
            assert owner_role == {
                "agent_paused_at": paused_at,
                "agent_paused_reason": "paused by workspace control",
                "version": 12,
            }
            related_role = connection.execute(
                text(
                    """
                    SELECT agent_paused_at, agent_paused_reason, version
                    FROM roles
                    WHERE id = 2002
                    """
                )
            ).mappings().one()
            assert related_role == {
                "agent_paused_at": None,
                "agent_paused_reason": None,
                "version": 4,
            }

            role_event = connection.execute(
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
            ).mappings().one()
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

            workspace_event = connection.execute(
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
            ).mappings().one()
            assert workspace_event["actor_user_id"] is None
            assert workspace_event["actor_name"] == "Taali migration"
            assert workspace_event["action"] == "migrated"
            assert workspace_event["from_version"] == 7
            assert workspace_event["to_version"] == 8
            assert "no role was resumed" in workspace_event["reason"]
            assert workspace_event["request_id"] == (
                "migration:182_workspace_pause_compat_audit:1001"
            )

            compatibility_audit = connection.execute(
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
            ).mappings().one()
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
