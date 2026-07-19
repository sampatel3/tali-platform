"""Populated SQLite migration contracts for dialect-specific compatibility paths."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.scripts.database_migrate import _alembic_config


def _upgrade(database_url: str, revision: str) -> None:
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.upgrade(_alembic_config(), revision)


def _engine(database_url: str):
    return create_engine(database_url, poolclass=NullPool)


def _insert_organization(connection, *, requirements: str | None = None) -> None:
    if requirements is None:
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
                    fireflies_single_account_mode,
                    workable_connected
                ) VALUES (
                    1,
                    'Migration sentinel',
                    false,
                    false,
                    'none',
                    0,
                    false,
                    true
                )
                """
            )
        )
        return
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
                fireflies_single_account_mode,
                default_additional_requirements,
                workable_connected
            ) VALUES (
                1,
                'Migration sentinel',
                false,
                false,
                'none',
                0,
                false,
                :requirements,
                true
            )
            """
        ),
        {"requirements": requirements},
    )


def test_revision_060_preserves_multiline_requirement_order(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'revision-060.sqlite3'}"
    _upgrade(database_url, "059_add_share_links")
    engine = _engine(database_url)
    requirements = "  Python  \r\n\nTeam leadership\n\tSystems thinking\t"
    try:
        with engine.begin() as connection:
            _insert_organization(connection, requirements=requirements)

        _upgrade(database_url, "060_add_settings_redesign_fields")

        with engine.connect() as connection:
            encoded = connection.execute(
                text("SELECT default_role_requirements FROM organizations WHERE id = 1")
            ).scalar_one()
        assert json.loads(encoded) == [
            "Python",
            "Team leadership",
            "Systems thinking",
        ]
    finally:
        engine.dispose()


def test_revisions_100_to_102_preserve_rows_and_backfill_values(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'revisions-100-102.sqlite3'}"
    _upgrade(database_url, "099_merge_098_heads")
    engine = _engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_organization(connection)
            connection.execute(
                text(
                    "INSERT INTO roles (id, organization_id, name, source) "
                    "VALUES (11, 1, 'Sentinel role', 'manual')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO candidates "
                    "(id, organization_id, full_name, phone) "
                    "VALUES (13, 1, 'Sentinel candidate', '+971 (050) 123-4567')"
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
                        notes,
                        source,
                        pipeline_stage,
                        pipeline_stage_updated_at,
                        pipeline_stage_source,
                        application_outcome,
                        application_outcome_updated_at,
                        version,
                        cv_match_score,
                        pre_screen_score_100,
                        pre_screen_evidence
                    ) VALUES (
                        17,
                        1,
                        13,
                        11,
                        'active',
                        'application sentinel',
                        'manual',
                        'review',
                        '2026-07-01 12:00:00',
                        'manual',
                        'active',
                        '2026-07-01 12:00:00',
                        3,
                        91.0,
                        12.0,
                        '{"llm_score_100": "82.75"}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO capability_flags (
                        capability,
                        organization_id,
                        enabled,
                        scope_json,
                        requires_json,
                        rolled_out_by
                    ) VALUES (
                        'sentinel_capability',
                        1,
                        true,
                        '{}',
                        '[]',
                        'migration-test'
                    )
                    """
                )
            )

        _upgrade(database_url, "102_add_genuine_pre_screen_score")

        with engine.begin() as connection:
            capability = connection.execute(
                text(
                    "SELECT id, capability, organization_id "
                    "FROM capability_flags WHERE capability = 'sentinel_capability'"
                )
            ).one()
            assert capability.id is not None
            assert capability.capability == "sentinel_capability"
            assert capability.organization_id == 1

            # Revision 100 exists specifically to make global rows possible.
            connection.execute(
                text(
                    """
                    INSERT INTO capability_flags (
                        capability,
                        organization_id,
                        enabled,
                        scope_json,
                        requires_json,
                        rolled_out_by
                    ) VALUES (
                        'global_sentinel',
                        NULL,
                        false,
                        '{}',
                        '[]',
                        'migration-test'
                    )
                    """
                )
            )
            assert (
                connection.execute(
                    text(
                        "SELECT id FROM capability_flags "
                        "WHERE capability = 'global_sentinel'"
                    )
                ).scalar_one()
                is not None
            )

            candidate = connection.execute(
                text("SELECT full_name, phone_normalized FROM candidates WHERE id = 13")
            ).one()
            assert candidate == ("Sentinel candidate", "501234567")
            application = connection.execute(
                text(
                    "SELECT notes, genuine_pre_screen_score_100 "
                    "FROM candidate_applications WHERE id = 17"
                )
            ).one()
            assert application.notes == "application sentinel"
            assert application.genuine_pre_screen_score_100 == 82.75
    finally:
        engine.dispose()


def test_revisions_149_to_179_preserve_rows_constraints_and_backfills(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'revisions-149-179.sqlite3'}"
    _upgrade(database_url, "148_add_outreach_campaigns")
    engine = _engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_organization(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id,
                        email,
                        hashed_password,
                        organization_id,
                        is_active,
                        is_superuser,
                        is_verified,
                        role
                    ) VALUES (
                        7,
                        'migration-sentinel@example.com',
                        'not-a-real-password-hash',
                        1,
                        true,
                        false,
                        false,
                        'owner'
                    )
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
                        description,
                        job_spec_text,
                        job_spec_filename,
                        job_spec_uploaded_at
                    ) VALUES (
                        11,
                        1,
                        'Sentinel role',
                        'manual',
                        'Stale ATS description',
                        'Recruiter-authored specification',
                        'custom-role-specification.pdf',
                        '2026-07-02 09:30:00'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "INSERT INTO candidates (id, organization_id, full_name) "
                    "VALUES (13, 1, 'Sentinel candidate')"
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
                        notes,
                        source,
                        pipeline_stage,
                        pipeline_stage_updated_at,
                        pipeline_stage_source,
                        application_outcome,
                        application_outcome_updated_at,
                        version
                    ) VALUES (
                        17,
                        1,
                        13,
                        11,
                        'active',
                        'application sentinel',
                        'manual',
                        'review',
                        '2026-07-02 10:00:00',
                        'manual',
                        'active',
                        '2026-07-02 10:00:00',
                        4
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO application_interviews (
                        id,
                        organization_id,
                        application_id,
                        provider,
                        provider_meeting_id
                    ) VALUES (19, 1, 17, 'fireflies', 'sentinel-meeting')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO interview_feedback (
                        id,
                        organization_id,
                        application_id,
                        role_id,
                        overall_recommendation,
                        notes,
                        created_at
                    ) VALUES (
                        23,
                        1,
                        17,
                        11,
                        'hire',
                        'feedback sentinel',
                        '2026-07-02 11:00:00'
                    )
                    """
                )
            )

        _upgrade(database_url, "149_extend_interview_feedback")
        with engine.connect() as connection:
            feedback = connection.execute(
                text(
                    "SELECT notes, submitted_at, interview_id "
                    "FROM interview_feedback WHERE id = 23"
                )
            ).one()
            assert feedback.notes == "feedback sentinel"
            assert feedback.submitted_at is not None
            assert feedback.interview_id is None

        _upgrade(database_url, "152_add_source_attribution_and_dispositions")
        with engine.begin() as connection:
            stages = connection.execute(
                text(
                    "SELECT slug, kind, position FROM pipeline_stages "
                    "WHERE organization_id = 1 ORDER BY position"
                )
            ).all()
            assert stages == [
                ("applied", "applied", 0),
                ("invited", "assessment", 1),
                ("in_assessment", "assessment", 2),
                ("review", "review", 3),
                ("advanced", "interview", 4),
            ]
            reasons = connection.execute(
                text(
                    "SELECT label, category, position "
                    "FROM disqualification_reasons "
                    "WHERE organization_id = 1 ORDER BY position"
                )
            ).all()
            assert len(reasons) == 11
            assert reasons[0] == ("Underqualified", "we_rejected", 0)
            assert reasons[-1] == ("Other", "other", 10)
            assert (
                connection.execute(
                    text("SELECT sync_mode FROM organizations WHERE id = 1")
                ).scalar_one()
                == "workable_primary"
            )
            assert (
                connection.execute(
                    text("SELECT stage_kind FROM candidate_applications WHERE id = 17")
                ).scalar_one()
                == "review"
            )
            reason_id = connection.execute(
                text(
                    "SELECT id FROM disqualification_reasons "
                    "WHERE organization_id = 1 AND label = 'Underqualified'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    UPDATE candidate_applications
                    SET source_strategy = 'referral',
                        source_name = 'migration sentinel source',
                        credited_to_user_id = 7,
                        disposition_reason_id = :reason_id,
                        disposition_category = 'we_rejected'
                    WHERE id = 17
                    """
                ),
                {"reason_id": reason_id},
            )

        _upgrade(database_url, "158_drop_pipeline_stages_and_dispositions")
        with engine.connect() as connection:
            application = connection.execute(
                text(
                    "SELECT notes, source_strategy, source_name, "
                    "credited_to_user_id FROM candidate_applications WHERE id = 17"
                )
            ).one()
            assert application == (
                "application sentinel",
                "referral",
                "migration sentinel source",
                7,
            )
            assert {
                "disposition_reason_id",
                "disposition_category",
                "stage_kind",
            }.isdisjoint(
                {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "candidate_applications"
                    )
                }
            )
            application_foreign_keys = inspect(connection).get_foreign_keys(
                "candidate_applications"
            )
            assert any(
                foreign_key["constrained_columns"] == ["credited_to_user_id"]
                and foreign_key["referred_table"] == "users"
                for foreign_key in application_foreign_keys
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM interview_feedback WHERE id = 23")
                ).scalar_one()
                == 1
            )

        _upgrade(database_url, "164_merge_agent_search_heads")
        with engine.connect() as connection:
            role = connection.execute(
                text(
                    "SELECT description, job_spec_text, "
                    "job_spec_manually_edited_at FROM roles WHERE id = 11"
                )
            ).one()
            assert role.description == "Recruiter-authored specification"
            assert role.job_spec_text == "Recruiter-authored specification"
            assert role.job_spec_manually_edited_at is not None

        _upgrade(database_url, "178_cv_score_dispatch_approval")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE users SET is_active = NULL, is_superuser = NULL "
                    "WHERE id = 7"
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO role_intents (
                        id,
                        organization_id,
                        role_id,
                        version,
                        structured_fields,
                        free_text,
                        superseded_id,
                        valid_from,
                        authored_by_user_id
                    ) VALUES
                        (
                            900,
                            1,
                            11,
                            1,
                            '{}',
                            'parent sentinel',
                            NULL,
                            '2026-07-03 08:00:00',
                            7
                        ),
                        (
                            901,
                            1,
                            11,
                            2,
                            '{}',
                            'child sentinel',
                            900,
                            '2026-07-03 09:00:00',
                            7
                        )
                    """
                )
            )

        _upgrade(database_url, "179_restore_schema_metadata_invariants")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT is_active, is_superuser FROM users WHERE id = 7")
            ).one() == (0, 0)
            assert connection.execute(
                text(
                    "SELECT id, free_text, superseded_id FROM role_intents ORDER BY id"
                )
            ).all() == [
                (900, "parent sentinel", None),
                (901, "child sentinel", 900),
            ]
            role_intent_foreign_keys = inspect(connection).get_foreign_keys(
                "role_intents"
            )
            assert any(
                foreign_key["name"] == "fk_role_intents_superseded_id"
                and foreign_key["constrained_columns"] == ["superseded_id"]
                and foreign_key["referred_table"] == "role_intents"
                for foreign_key in role_intent_foreign_keys
            )
    finally:
        engine.dispose()


def test_revisions_192_to_194_preserve_score_jobs_and_enforce_active_ownership(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'revisions-192-194.sqlite3'}"
    _upgrade(database_url, "191_task_repo_identity")
    engine = _engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_organization(connection)
            connection.execute(
                text(
                    "INSERT INTO roles (id, organization_id, name, source) "
                    "VALUES (11, 1, 'Scoring migration sentinel', 'manual')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO candidates (id, organization_id, full_name) "
                    "VALUES (13, 1, 'Scoring migration candidate')"
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
                        notes,
                        source,
                        pipeline_stage,
                        pipeline_stage_updated_at,
                        pipeline_stage_source,
                        application_outcome,
                        application_outcome_updated_at,
                        version
                    ) VALUES (
                        17,
                        1,
                        13,
                        11,
                        'active',
                        'score-job migration sentinel',
                        'manual',
                        'review',
                        '2026-07-19 08:00:00',
                        'manual',
                        'active',
                        '2026-07-19 08:00:00',
                        3
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO background_job_runs (
                        id,
                        kind,
                        scope_kind,
                        scope_id,
                        organization_id,
                        status,
                        counters,
                        started_at,
                        finished_at
                    ) VALUES
                        (
                            41,
                            'scoring_batch',
                            'role',
                            11,
                            1,
                            'dispatching',
                            '{"sentinel": 1}',
                            '2026-07-19 08:01:00',
                            NULL
                        ),
                        (
                            42,
                            'scoring_batch',
                            'role',
                            11,
                            1,
                            'completed',
                            '{"sentinel": 2}',
                            '2026-07-19 07:00:00',
                            '2026-07-19 07:30:00'
                        )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO cv_score_jobs (
                        id,
                        application_id,
                        role_id,
                        status,
                        cache_key,
                        prompt_version,
                        model,
                        cache_hit,
                        error_message,
                        celery_task_id,
                        requires_active_agent,
                        force_full_score,
                        dispatch_approved,
                        queued_at,
                        finished_at
                    ) VALUES (
                        31,
                        17,
                        11,
                        'done',
                        'preserved-cache-key',
                        'preserved-prompt',
                        'preserved-model',
                        'miss',
                        'preserved terminal evidence',
                        'preserved-task-id',
                        false,
                        true,
                        true,
                        '2026-07-19 08:02:00',
                        '2026-07-19 08:03:00'
                    )
                    """
                )
            )

        _upgrade(database_url, "head")

        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "195_compatibility_invariant_hardening"
            )
            assert connection.execute(
                text(
                    """
                    SELECT
                        application_id,
                        role_id,
                        status,
                        cache_key,
                        prompt_version,
                        model,
                        cache_hit,
                        error_message,
                        celery_task_id,
                        requires_active_agent,
                        force_full_score,
                        dispatch_approved,
                        batch_run_id
                    FROM cv_score_jobs
                    WHERE id = 31
                    """
                )
            ).one() == (
                17,
                11,
                "done",
                "preserved-cache-key",
                "preserved-prompt",
                "preserved-model",
                "miss",
                "preserved terminal evidence",
                "preserved-task-id",
                0,
                1,
                1,
                None,
            )
            assert connection.execute(
                text(
                    """
                    SELECT id
                    FROM background_job_runs
                         INDEXED BY ix_background_job_runs_scoring_recovery_active
                    WHERE kind = 'scoring_batch'
                      AND finished_at IS NULL
                      AND status IN (
                          'dispatching', 'queued', 'running', 'cancelling'
                      )
                    ORDER BY scope_kind, id
                    """
                )
            ).scalars().all() == [41]

            connection.execute(
                text("UPDATE cv_score_jobs SET batch_run_id = 41 WHERE id = 31")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO cv_score_jobs (
                        id, application_id, role_id, batch_run_id, status
                    ) VALUES
                        (32, 17, 11, 41, 'error'),
                        (33, 17, 11, 41, 'pending'),
                        (34, 17, 11, NULL, 'pending'),
                        (35, 17, 11, NULL, 'running')
                    """
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO cv_score_jobs (
                            id, application_id, role_id, batch_run_id, status
                        ) VALUES (36, 17, 11, 41, 'running')
                        """
                    )
                )

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id FROM cv_score_jobs ORDER BY id")
            ).scalars().all() == [31, 32, 33, 34, 35]
    finally:
        engine.dispose()
