"""Bounded contracts for production PostgreSQL semantics SQLite cannot prove.

The normal backend suite intentionally stays on fast, isolated SQLite. These
tests migrate one disposable PostgreSQL database and execute only the small
set of dialect-specific behavior the application relies on directly: JSON
array search, transaction advisory locks, append-only/unique constraints, and
``FOR UPDATE SKIP LOCKED`` outbox claims.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.actions import approve_decision
from app.actions.types import ACTOR_RECRUITER, Actor
from app.brain_feed import outbox as brain_feed_outbox
from app.candidate_search.query_builder_sql import apply_parsed_filter
from app.candidate_search.schemas import ParsedFilter
from app.candidate_graph.ingest_manifest import manifest_sha256
from app.domains.assessments_runtime.application_mutation_authorization import (
    require_application_job_permission,
)
from app.domains.assessments_runtime.pipeline_service import transition_outcome
from app.domains.assessments_runtime.workspace_serialization import (
    ASSESSMENT_WORKSPACE_LOCK_SCOPE,
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)
from app.domains.assessments_runtime.job_authorization import JobPermission
from app.domains.assessments_runtime.related_role_actions import (
    require_related_role_application_action,
)
from app.models.brain_feed_outbox import (
    BRAIN_FEED_STATUS_PENDING,
    BRAIN_FEED_STATUS_PROCESSING,
    BrainFeedOutbox,
)
from app.models.assessment import Assessment
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_decision import AgentDecision
from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.anthropic_batch_result_receipt import (
    AnthropicBatchResultReceipt,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.claude_call_log import ClaudeCallLog
from app.models.job_hiring_team import TEAM_ROLE_RECRUITER, JobHiringTeam
from app.models.graph_ingest_dispatch import GraphIngestDispatch
from app.models.organization import Organization
from app.models.policy_version import PolicyVersion
from app.models.role import Role, role_tasks
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.models.task import Task
from app.models.usage_event import UsageEvent
from app.models.workspace_pause_migration_audit import WorkspacePauseMigrationAudit
from app.services.ats_operation_guards import lock_live_application_move
from app.services import anthropic_batch_recovery, anthropic_batch_result_metering
from app.services.assessment_repository_operations import (
    create_serialized_assessment_branch,
)
from app.services.auto_reject_op import execute_auto_reject_op
from app.services.auto_reject_operation_receipt import AUTO_REJECT_OPERATION_KEY
from app.services import cv_gap_rejection_batch
from app.services.bulk_decision_service.stage_toggle import (
    reconcile_pending_positive_decisions,
)
from app.services.provider_usage_admission import serialize_provider_work
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.role_execution_guard import lock_live_role
from app.services.task_repository_serialization import (
    TASK_REPOSITORY_WRITE_LOCK_SCOPE,
    TaskRepositoryBusyError,
    task_repository_write_mutex,
)
from app.services import task_catalog
from app.services.task_catalog import (
    TASK_CATALOG_SYNC_LOCK_SCOPE,
    serialize_task_catalog_sync,
    sync_template_task_specs,
)
from app.platform.database import (
    register_workspace_lock_engine_factory,
    unregister_workspace_lock_engine_factory,
)
from app.decision_policy import nightly_policy_fit
from app.decision_policy.fit_serialization import POLICY_FIT_LOCK_SCOPE
from app.decision_policy.fitted_policy import FittedModel, TrainingExample
from tests.postgres_support import (
    configured_test_postgres_url,
    isolated_postgres_database,
    run_database_migrator,
)


@pytest.fixture(scope="module")
def postgres_runtime_engine() -> Iterator[Engine]:
    if not configured_test_postgres_url():
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL runtime contracts")

    with isolated_postgres_database(prefix="runtime_contract") as database_url:
        result = run_database_migrator(database_url)
        assert result.returncode == 0, result.stdout + result.stderr
        engine = create_engine(database_url, poolclass=NullPool)
        register_workspace_lock_engine_factory(
            engine,
            lambda: create_engine(
                database_url,
                pool_size=10,
                max_overflow=0,
                pool_timeout=5,
                pool_pre_ping=True,
            ),
        )
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "189_shared_family_reject_repair"
                )
            yield engine
        finally:
            unregister_workspace_lock_engine_factory(engine)
            engine.dispose()


@pytest.fixture
def postgres_db(postgres_runtime_engine: Engine) -> Iterator[Session]:
    """Rollback-only session for tests that do not need cross-session commits."""

    connection = postgres_runtime_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _seed_application(db: Session, *, prefix: str) -> CandidateApplication:
    organization = Organization(name=f"PG contract {prefix}", slug=prefix)
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=int(organization.id),
        name="Platform Engineer",
        source="manual",
    )
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"{prefix}@example.test",
        full_name="PostgreSQL Contract Candidate",
        position="Engineering Lead",
        skills=["Python", "Amazon Web Services (AWS)"],
        experience_entries=[
            {
                "title": "Senior Project Manager",
                "country": "United Kingdom",
                "start_date": "2017-06",
            }
        ],
    )
    db.add_all([role, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    return application


def _seed_pending_positive_decision(
    db: Session,
    *,
    prefix: str,
) -> tuple[CandidateApplication, AgentDecision]:
    application = _seed_application(db, prefix=prefix)
    decision = AgentDecision(
        organization_id=int(application.organization_id),
        role_id=int(application.role_id),
        application_id=int(application.id),
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="PostgreSQL lock-order contract",
        evidence={},
        model_version="postgres-contract",
        prompt_version="postgres-contract",
        idempotency_key=f"{prefix}:send-assessment",
    )
    db.add(decision)
    db.flush()
    return application, decision


class _AuthorityClaimObserved(RuntimeError):
    pass


def _assert_application_lock_precedes_workspace_authority(
    session_factory,
    *,
    organization_id: int,
    application_id: int,
    action: Callable[[Session], None],
) -> None:
    """Prove the live action holds Application while waiting on Organization."""

    from app.services import workspace_agent_control

    authority_attempted = Event()
    original_snapshot = workspace_agent_control.workspace_agent_control_snapshot

    def _observed_snapshot(*args, **kwargs):
        authority_attempted.set()
        original_snapshot(*args, **kwargs)
        # Stop immediately after the authority lock is granted so this ordering
        # contract cannot dispatch any downstream application side effect.
        raise _AuthorityClaimObserved

    def _run_action() -> None:
        with session_factory() as action_db:
            try:
                action(action_db)
            except _AuthorityClaimObserved:
                pass
            finally:
                action_db.rollback()

    blocker_db = session_factory()
    observer_db = session_factory()
    try:
        blocker_db.execute(
            text("SELECT id FROM organizations WHERE id = :org_id FOR UPDATE"),
            {"org_id": int(organization_id)},
        )
        with (
            patch.object(
                workspace_agent_control,
                "workspace_agent_control_snapshot",
                side_effect=_observed_snapshot,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(_run_action)
            try:
                reached_authority = authority_attempted.wait(timeout=3)
                if not reached_authority:
                    blocker_db.rollback()
                    future.result(timeout=5)
                assert reached_authority
                with pytest.raises(DBAPIError):
                    observer_db.execute(
                        text(
                            "SELECT id FROM candidate_applications "
                            "WHERE id = :application_id FOR UPDATE NOWAIT"
                        ),
                        {"application_id": int(application_id)},
                    )
                observer_db.rollback()
            finally:
                blocker_db.rollback()
            future.result(timeout=5)
    finally:
        observer_db.rollback()
        blocker_db.rollback()
        observer_db.close()
        blocker_db.close()


def test_related_role_and_migration_audit_history_is_database_protected(
    postgres_db: Session,
):
    application = _seed_application(postgres_db, prefix="history-protection")
    owner_role = postgres_db.get(Role, int(application.role_id))
    assert owner_role is not None
    related_role = Role(
        organization_id=int(application.organization_id),
        name="Preserved alternate role",
        source="manual",
        role_kind="sister",
        ats_owner_role_id=int(owner_role.id),
    )
    postgres_db.add(related_role)
    postgres_db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=int(application.organization_id),
        role_id=int(related_role.id),
        source_application_id=int(application.id),
        status="done",
        spec_fingerprint="a" * 64,
        summary="This historical score must survive role lifecycle changes.",
    )
    audit = WorkspacePauseMigrationAudit(
        organization_id=int(application.organization_id),
        migration_revision="postgres_runtime_contract",
        evidence_source="test_evidence",
        evidence_quality="exact",
        converted_role_count=0,
        source_role_event_ids=[],
        source_role_ids=[],
        compatibility_applied=False,
        control_version_before=1,
        control_version_after=1,
        anomalies=[],
    )
    postgres_db.add_all([evaluation, audit])
    postgres_db.flush()

    with pytest.raises(IntegrityError), postgres_db.begin_nested():
        postgres_db.execute(delete(Role).where(Role.id == int(owner_role.id)))
        postgres_db.flush()
    with pytest.raises(IntegrityError), postgres_db.begin_nested():
        postgres_db.execute(delete(Role).where(Role.id == int(related_role.id)))
        postgres_db.flush()
    with pytest.raises(DBAPIError), postgres_db.begin_nested():
        postgres_db.execute(
            text(
                "UPDATE workspace_pause_migration_audits "
                "SET anomalies = anomalies WHERE id = :audit_id"
            ),
            {"audit_id": int(audit.id)},
        )
        postgres_db.flush()
    with pytest.raises(DBAPIError), postgres_db.begin_nested():
        postgres_db.execute(
            text("DELETE FROM workspace_pause_migration_audits WHERE id = :audit_id"),
            {"audit_id": int(audit.id)},
        )
        postgres_db.flush()

    assert postgres_db.get(Role, int(owner_role.id)) is not None
    assert postgres_db.get(Role, int(related_role.id)) is not None
    assert postgres_db.get(SisterRoleEvaluation, int(evaluation.id)) is not None
    assert postgres_db.get(WorkspacePauseMigrationAudit, int(audit.id)) is not None


def test_ats_move_lock_serializes_a_concurrent_close(
    postgres_runtime_engine: Engine,
) -> None:
    """A close cannot commit between the worker's live check and provider I/O."""

    with Session(postgres_runtime_engine, expire_on_commit=False) as seed_db:
        application = _seed_application(
            seed_db,
            prefix=f"pg-ats-lock-{uuid4().hex}",
        )
        seed_db.commit()
        organization_id = int(application.organization_id)
        application_id = int(application.id)

    with (
        Session(postgres_runtime_engine) as move_db,
        Session(postgres_runtime_engine) as close_db,
    ):
        locked = lock_live_application_move(
            move_db,
            organization_id=organization_id,
            application_id=application_id,
        )
        assert int(locked.id) == application_id

        close_db.execute(text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(DBAPIError):
            close_db.execute(
                text(
                    "UPDATE candidate_applications "
                    "SET application_outcome = 'withdrawn' WHERE id = :app_id"
                ),
                {"app_id": application_id},
            )
        close_db.rollback()

        # Provider confirmation/local projection would commit at this point.
        move_db.commit()
        close_db.execute(
            text(
                "UPDATE candidate_applications "
                "SET application_outcome = 'withdrawn' WHERE id = :app_id"
            ),
            {"app_id": application_id},
        )
        close_db.commit()


def test_assessment_stage_reconcile_skips_locked_app_and_decision_rows(
    postgres_runtime_engine: Engine,
) -> None:
    """Reflow never waits app/decision -> authority in the inverse order."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-stage-reconcile-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application, decision = _seed_pending_positive_decision(
            seed_db,
            prefix=prefix,
        )
        role = seed_db.get(Role, int(application.role_id))
        assert role is not None
        organization_id = int(application.organization_id)
        application_id = int(application.id)
        decision_id = int(decision.id)
        role_id = int(role.id)
        role_version = int(role.version or 1)

    blocker_db = session_factory()
    reconcile_db = session_factory()
    try:
        blocker_db.execute(
            text(
                "SELECT id FROM candidate_applications "
                "WHERE id = :application_id FOR UPDATE"
            ),
            {"application_id": application_id},
        )
        blocker_db.execute(
            text("SELECT id FROM agent_decisions WHERE id = :decision_id FOR UPDATE"),
            {"decision_id": decision_id},
        )
        reconcile_db.execute(text("SET LOCAL lock_timeout = '150ms'"))

        assert (
            reconcile_pending_positive_decisions(
                reconcile_db,
                role_id=role_id,
                expected_role_version=role_version,
            )
            == 0
        )
        # A swallowed lock timeout rolls the session back. Remaining inside the
        # same live transaction proves both contested rows were SKIP LOCKED.
        assert reconcile_db.in_transaction()
        persisted = reconcile_db.get(AgentDecision, decision_id)
        assert persisted is not None
        assert persisted.status == "pending"
        assert int(persisted.organization_id) == organization_id
    finally:
        reconcile_db.rollback()
        blocker_db.rollback()
        reconcile_db.close()
        blocker_db.close()


def test_approve_decision_locks_application_before_workspace_authority(
    postgres_runtime_engine: Engine,
) -> None:
    """A manual action holds its app before waiting on Organization."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-approve-order-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application, decision = _seed_pending_positive_decision(
            seed_db,
            prefix=prefix,
        )
        organization_id = int(application.organization_id)
        application_id = int(application.id)
        decision_id = int(decision.id)

    _assert_application_lock_precedes_workspace_authority(
        session_factory,
        organization_id=organization_id,
        application_id=application_id,
        action=lambda action_db: approve_decision.run(
            action_db,
            Actor(type=ACTOR_RECRUITER),
            organization_id=organization_id,
            decision_id=decision_id,
        ),
    )


def test_batch_accept_rejects_an_interleaved_decision_type_flip(
    postgres_runtime_engine: Engine,
) -> None:
    """A bulk accept cannot turn a displayed send into an unseen reject."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-decision-type-race-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application, decision = _seed_pending_positive_decision(
            seed_db,
            prefix=prefix,
        )
        organization_id = int(application.organization_id)
        decision_id = int(decision.id)

    with session_factory() as preview_db:
        displayed = preview_db.get(AgentDecision, decision_id)
        assert displayed is not None
        assert displayed.decision_type == "send_assessment"

        with session_factory.begin() as scoring_db:
            current = scoring_db.get(AgentDecision, decision_id)
            assert current is not None
            current.decision_type = "reject"
            current.recommendation = "reject"

        result = approve_decision.enqueue_batch(
            preview_db,
            Actor(type=ACTOR_RECRUITER),
            organization_id=organization_id,
            decision_ids=[decision_id],
            expected_decision_types={str(decision_id): "send_assessment"},
        )

    assert result["accepted"] == []
    assert result["failures"][0]["detail"]["code"] == "DECISION_CHANGED"
    with session_factory() as verify_db:
        persisted = verify_db.get(AgentDecision, decision_id)
        assert persisted is not None
        assert persisted.status == "pending"
        assert persisted.decision_type == "reject"


def test_related_reject_holds_family_lock_through_the_side_effect(
    postgres_runtime_engine: Engine,
) -> None:
    """Family membership cannot change after worker validation but before I/O."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-related-reject-lock-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application, decision = _seed_pending_positive_decision(
            seed_db,
            prefix=prefix,
        )
        owner = seed_db.get(Role, int(application.role_id))
        assert owner is not None
        related = Role(
            organization_id=int(application.organization_id),
            name="Related reject authority",
            source="sister",
            role_kind="sister",
            ats_owner_role_id=int(owner.id),
        )
        seed_db.add(related)
        seed_db.flush()
        decision.role_id = int(related.id)
        decision.decision_type = "reject"
        decision.recommendation = "reject"
        decision.status = "processing"
        organization_id = int(application.organization_id)
        decision_id = int(decision.id)
        related_id = int(related.id)
        displayed_family = {
            "owner": {"id": int(owner.id), "name": str(owner.name)},
            "related": [{"id": related_id, "name": str(related.name)}],
        }

    effect_entered = Event()
    release_effect = Event()

    def _hold_effect(*_args, **_kwargs):
        effect_entered.set()
        assert release_effect.wait(timeout=5)
        raise _AuthorityClaimObserved

    def _run_action() -> None:
        with session_factory() as action_db:
            try:
                with patch(
                    "app.actions.approve_decision.reject_application.run",
                    side_effect=_hold_effect,
                ):
                    approve_decision.run(
                        action_db,
                        Actor(type=ACTOR_RECRUITER),
                        organization_id=organization_id,
                        decision_id=decision_id,
                        expected_decision_type="reject",
                        expected_role_family=displayed_family,
                    )
            except _AuthorityClaimObserved:
                pass
            finally:
                action_db.rollback()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_action)
        try:
            assert effect_entered.wait(timeout=5)
            with session_factory() as observer_db:
                observer_db.execute(text("SET LOCAL lock_timeout = '150ms'"))
                with pytest.raises(DBAPIError):
                    observer_db.execute(
                        text(
                            "SELECT id FROM roles WHERE id = :role_id FOR UPDATE NOWAIT"
                        ),
                        {"role_id": related_id},
                    )
                observer_db.rollback()
        finally:
            release_effect.set()
        future.result(timeout=5)


def test_auto_reject_claim_locks_application_before_workspace_authority(
    postgres_runtime_engine: Engine,
) -> None:
    """The deferred ATS claim cannot race a concurrent manual close."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-auto-reject-order-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization_id = int(application.organization_id)
        application_id = int(application.id)

    _assert_application_lock_precedes_workspace_authority(
        session_factory,
        organization_id=organization_id,
        application_id=application_id,
        action=lambda action_db: execute_auto_reject_op(
            action_db,
            organization_id,
            {
                "application_id": application_id,
                "actor_type": "auto",
                "receipt_key": f"{prefix}:receipt",
            },
        ),
    )


def test_auto_reject_provider_io_holds_no_application_or_authority_row_locks(
    postgres_runtime_engine: Engine,
) -> None:
    """Application, Organization, and Role are all free during ATS I/O."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-auto-reject-provider-phase-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization_id = int(application.organization_id)
        application_id = int(application.id)
        role_id = int(application.role_id)
        provider_target_id = f"wk-{uuid4().hex}"
        application.workable_candidate_id = provider_target_id
        role = seed_db.get(Role, role_id)
        assert role is not None
        role.agentic_mode_enabled = True
        role.auto_reject = True

    def _deferred_decision(**kwargs) -> dict:
        kwargs["app"].auto_reject_state = "provider_writeback_in_progress"
        return {
            "performed": False,
            "state": "provider_writeback_in_progress",
            "reason": "Below threshold",
            "provider_writeback_required": True,
            "provider": "workable",
            "provider_target_id": provider_target_id,
            "config": {"threshold_100": 50},
            "snapshot": {"pre_screen_score": 10},
        }

    def _provider_call(**_kwargs) -> dict:
        # NOWAIT is the PostgreSQL proof: this second connection would raise if
        # the worker retained any claim-phase row lock across network I/O.
        with session_factory() as observer:
            observer.execute(text("SET LOCAL lock_timeout = '150ms'"))
            for table, row_id in (
                ("candidate_applications", application_id),
                ("organizations", organization_id),
                ("roles", role_id),
            ):
                assert (
                    observer.execute(
                        text(
                            f"SELECT id FROM {table} WHERE id = :id FOR UPDATE NOWAIT"
                        ),
                        {"id": row_id},
                    ).scalar_one()
                    == row_id
                )
            observer.rollback()
        return {"success": True, "action": "disqualify", "code": "ok"}

    with session_factory() as worker_db:
        with (
            patch(
                "app.services.application_automation_service.run_auto_reject_if_needed",
                side_effect=_deferred_decision,
            ),
            patch(
                "app.services.workable_actions_service.disqualify_candidate_in_workable",
                side_effect=_provider_call,
            ),
        ):
            result = execute_auto_reject_op(
                worker_db,
                organization_id,
                {
                    "application_id": application_id,
                    "actor_type": "auto",
                    "receipt_key": f"{prefix}:receipt",
                },
            )

    assert result == {
        "status": "ok",
        "application_id": application_id,
        "performed": True,
        "provider": "workable",
    }


def test_stage_move_provider_io_holds_no_application_or_authority_row_locks(
    postgres_runtime_engine: Engine,
) -> None:
    """The stage provider callback runs after every claim-phase row lock commits."""

    from app.services.ats_stage_move_dispatch_snapshot import (
        build_stage_move_dispatch_payload,
    )
    from app.services.ats_stage_move_lifecycle import execute_stage_move_lifecycle

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-stage-move-provider-phase-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization = seed_db.get(Organization, int(application.organization_id))
        candidate = seed_db.get(Candidate, int(application.candidate_id))
        role = seed_db.get(Role, int(application.role_id))
        assert organization is not None and candidate is not None and role is not None
        organization.workable_connected = True
        organization.workable_access_token = "postgres-stage-token"
        organization.workable_subdomain = "postgres-stage"
        organization.workable_config = {
            "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
            "workable_writeback": True,
            "workable_actor_member_id": "postgres-stage-member",
        }
        role.source = "workable"
        role.workable_job_id = f"job-{prefix}"
        application.source = "workable"
        application.workable_candidate_id = f"candidate-{prefix}"
        payload = build_stage_move_dispatch_payload(
            app=application,
            owner_role=role,
            provider="workable",
            target_stage="technical-interview",
        )
        organization_id = int(organization.id)
        application_id = int(application.id)
        candidate_id = int(candidate.id)
        role_id = int(role.id)

    with session_factory() as worker_db:

        def _provider_call(plan) -> dict:
            assert not worker_db.in_transaction()
            with session_factory() as observer:
                observer.execute(text("SET LOCAL lock_timeout = '150ms'"))
                for table, row_id in (
                    ("candidate_applications", application_id),
                    ("candidates", candidate_id),
                    ("organizations", organization_id),
                    ("roles", role_id),
                ):
                    assert (
                        observer.execute(
                            text(
                                f"SELECT id FROM {table} WHERE id = :id FOR UPDATE NOWAIT"
                            ),
                            {"id": row_id},
                        ).scalar_one()
                        == row_id
                    )
                observer.rollback()
            return {
                "success": True,
                "code": "ok",
                "provider": plan.provider,
                "provider_remote_stage": plan.target_stage,
            }

        result = execute_stage_move_lifecycle(
            worker_db,
            organization_id=organization_id,
            payload=payload,
            provider_call=_provider_call,
        )

    assert result["status"] == "ok"
    assert result["application_id"] == application_id


def test_cv_gap_provider_io_holds_no_application_or_authority_row_locks(
    postgres_runtime_engine: Engine,
) -> None:
    """The confirmed CV-gap worker releases every claim before provider I/O."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-cv-gap-provider-phase-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization_id = int(application.organization_id)
        application_id = int(application.id)
        candidate_id = int(application.candidate_id)
        role_id = int(application.role_id)
        role = seed_db.get(Role, role_id)
        assert role is not None
        role.agentic_mode_enabled = True
        role.job_spec_text = "Requirements\n- Python"
        actor = User(
            email=f"{prefix}-owner@example.test",
            hashed_password="x",
            is_active=True,
            is_verified=True,
            organization_id=organization_id,
            role="owner",
        )
        card = AgentNeedsInput(
            organization_id=organization_id,
            role_id=role_id,
            kind="missing_cv",
            prompt="One candidate has no CV.",
        )
        seed_db.add_all([actor, card])
        seed_db.flush()
        actor_id = int(actor.id)
        card_id = int(card.id)
        expected_version = int(role.version or 1)
        expected_family = {
            "owner": {"id": role_id, "name": str(role.name)},
            "related": [],
        }

    def _provider_phase(_db, **_kwargs) -> dict:
        with session_factory() as observer:
            observer.execute(text("SET LOCAL lock_timeout = '150ms'"))
            for table, row_id in (
                ("candidate_applications", application_id),
                ("candidates", candidate_id),
                ("roles", role_id),
                ("agent_needs_input", card_id),
                ("users", actor_id),
                ("organizations", organization_id),
            ):
                assert (
                    observer.execute(
                        text(
                            f"SELECT id FROM {table} WHERE id = :id FOR UPDATE NOWAIT"
                        ),
                        {"id": row_id},
                    ).scalar_one()
                    == row_id
                )
            observer.rollback()
        return {
            "provider": "local",
            "provider_target_id": "",
            "write_required": False,
            "success": True,
            "code": "local_only",
        }

    payload = {
        "role_id": role_id,
        "needs_input_id": card_id,
        "kind": "missing_cv",
        "user_id": actor_id,
        "application_ids": [application_id],
        "expected_owner_role_version": expected_version,
        "expected_role_family": expected_family,
    }
    with (
        session_factory() as worker_db,
        patch.object(
            cv_gap_rejection_batch,
            "perform_cv_gap_provider_reject",
            side_effect=_provider_phase,
        ) as provider_effect,
    ):
        result = cv_gap_rejection_batch.run_cv_gap_rejection_batch(
            worker_db,
            organization_id,
            payload,
        )

    provider_effect.assert_called_once()
    assert result["progress"]["rejected_application_ids"] == [application_id]


@pytest.mark.parametrize(
    ("mutation", "expected_outcome"),
    (
        ("hired", "hired"),
        ("withdrawn", "withdrawn"),
        ("application_version", "open"),
        ("role_authority", "open"),
    ),
)
def test_auto_reject_paused_provider_preserves_concurrent_local_authority(
    postgres_runtime_engine: Engine,
    mutation: str,
    expected_outcome: str,
) -> None:
    """Remote success is reconciled honestly, never over a newer local row."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-auto-reject-drift-{mutation}-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization_id = int(application.organization_id)
        application_id = int(application.id)
        role_id = int(application.role_id)
        provider_target_id = f"wk-{uuid4().hex}"
        application.workable_candidate_id = provider_target_id
        role = seed_db.get(Role, role_id)
        assert role is not None
        role.agentic_mode_enabled = True
        role.auto_reject = True

    provider_entered = Event()
    release_provider = Event()

    def _deferred_decision(**kwargs) -> dict:
        kwargs["app"].auto_reject_state = "provider_writeback_in_progress"
        return {
            "performed": False,
            "state": "provider_writeback_in_progress",
            "reason": "Below threshold",
            "provider_writeback_required": True,
            "provider": "workable",
            "provider_target_id": provider_target_id,
            "config": {"threshold_100": 50},
            "snapshot": {"pre_screen_score": 10},
        }

    def _provider_call(**_kwargs) -> dict:
        provider_entered.set()
        assert release_provider.wait(timeout=5)
        return {"success": True, "action": "disqualify", "code": "ok"}

    def _run_worker() -> dict:
        with session_factory() as worker_db:
            return execute_auto_reject_op(
                worker_db,
                organization_id,
                {
                    "application_id": application_id,
                    "actor_type": "auto",
                    "receipt_key": f"{prefix}:receipt",
                },
            )

    with (
        patch(
            "app.services.application_automation_service.run_auto_reject_if_needed",
            side_effect=_deferred_decision,
        ),
        patch(
            "app.services.workable_actions_service.disqualify_candidate_in_workable",
            side_effect=_provider_call,
        ),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(_run_worker)
        try:
            assert provider_entered.wait(timeout=5)
            with session_factory.begin() as concurrent:
                current = (
                    concurrent.query(CandidateApplication)
                    .filter(CandidateApplication.id == application_id)
                    .with_for_update()
                    .one()
                )
                if mutation in {"hired", "withdrawn"}:
                    transition_outcome(
                        concurrent,
                        app=current,
                        to_outcome=mutation,
                        actor_type="recruiter",
                        reason=f"Concurrent recruiter {mutation}",
                    )
                elif mutation == "application_version":
                    current.version = int(current.version or 1) + 1
                else:
                    role = (
                        concurrent.query(Role)
                        .filter(Role.id == role_id)
                        .with_for_update()
                        .one()
                    )
                    role.auto_reject = False
                    role.version = int(role.version or 1) + 1
        finally:
            release_provider.set()
        result = future.result(timeout=5)

    assert result["status"] == "manual_reconciliation_required"
    assert result["performed"] is False
    assert result["provider_performed"] is True
    with session_factory() as observer:
        persisted = observer.get(CandidateApplication, application_id)
        assert persisted is not None
        assert persisted.application_outcome == expected_outcome
        assert persisted.auto_reject_state == "manual_reconciliation_required"
        receipt = persisted.integration_sync_state[AUTO_REJECT_OPERATION_KEY]
        assert receipt["status"] == "manual_reconciliation_required"
        assert (
            observer.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == application_id,
                CandidateApplicationEvent.event_type
                == "auto_reject_manual_reconciliation_required",
            )
            .count()
            == 1
        )
        assert (
            observer.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == application_id,
                CandidateApplicationEvent.event_type == "auto_rejected",
            )
            .count()
            == 0
        )


def test_live_role_guard_locks_workspace_before_flushing_role_update(
    postgres_runtime_engine: Engine,
) -> None:
    """An unflushed Role UPDATE cannot precede workspace authority."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    prefix = f"pg-live-role-flush-order-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        organization_id = int(application.organization_id)
        role_id = int(application.role_id)

    blocker_db = session_factory()
    action_db = session_factory()
    try:
        blocker_db.execute(
            text("SELECT id FROM organizations WHERE id = :org_id FOR UPDATE"),
            {"org_id": organization_id},
        )
        role = action_db.get(Role, role_id)
        assert role is not None
        role.name = "Unflushed role mutation"
        action_db.execute(text("SET LOCAL lock_timeout = '150ms'"))

        with pytest.raises(DBAPIError):
            lock_live_role(
                action_db,
                role_id=role_id,
                organization_id=organization_id,
            )

        # The authority timeout occurred before db.flush(), so this transaction
        # never emitted the pending Role UPDATE or acquired its row lock.
        locked_role = (
            blocker_db.query(Role)
            .filter(Role.id == role_id)
            .with_for_update(nowait=True, of=Role)
            .one()
        )
        assert int(locked_role.id) == role_id
    finally:
        action_db.rollback()
        blocker_db.rollback()
        action_db.close()
        blocker_db.close()


def test_related_action_locks_application_before_related_role(
    postgres_runtime_engine: Engine,
) -> None:
    """A blocked action cannot hold the role needed by the ATS worker."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-related-lock-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        owner_role = seed_db.get(Role, int(application.role_id))
        assert owner_role is not None
        related_role = Role(
            organization_id=int(application.organization_id),
            name="Lock-ordered related role",
            source="sister",
            role_kind="sister",
            ats_owner_role_id=int(owner_role.id),
        )
        actor = User(
            email=f"{prefix}-actor@example.test",
            hashed_password="x",
            is_active=True,
            is_verified=True,
            organization_id=int(application.organization_id),
            role="member",
        )
        seed_db.add_all([related_role, actor])
        seed_db.flush()
        seed_db.add_all(
            [
                JobHiringTeam(
                    organization_id=int(application.organization_id),
                    role_id=int(related_role.id),
                    user_id=int(actor.id),
                    team_role=TEAM_ROLE_RECRUITER,
                ),
                SisterRoleEvaluation(
                    organization_id=int(application.organization_id),
                    role_id=int(related_role.id),
                    source_application_id=int(application.id),
                    status="done",
                    spec_fingerprint="b" * 64,
                ),
            ]
        )
        application_id = int(application.id)
        organization_id = int(application.organization_id)
        related_role_id = int(related_role.id)
        actor_id = int(actor.id)

    worker_db = session_factory()
    action_db = session_factory()
    try:
        lock_live_application_move(
            worker_db,
            organization_id=organization_id,
            application_id=application_id,
        )
        action_application = action_db.get(CandidateApplication, application_id)
        action_actor = action_db.get(User, actor_id)
        assert action_application is not None
        assert action_actor is not None
        action_db.execute(text("SET LOCAL lock_timeout = '150ms'"))

        with pytest.raises(DBAPIError):
            require_related_role_application_action(
                action_db,
                current_user=action_actor,
                related_role_id=related_role_id,
                application=action_application,
            )

        # Do not roll the failed action transaction back yet. If it had taken
        # the related-role lock before waiting on the application, this NOWAIT
        # acquisition would fail and expose the inverse lock order.
        locked_role = (
            worker_db.query(Role)
            .filter(Role.id == related_role_id)
            .with_for_update(nowait=True, of=Role)
            .one()
        )
        assert int(locked_role.id) == related_role_id

        # Release the simulated worker, then prove a successful authorization
        # keeps the canonical role lock until its mutation transaction ends.
        action_db.rollback()
        worker_db.rollback()
        action_application = action_db.get(CandidateApplication, application_id)
        action_actor = action_db.get(User, actor_id)
        assert action_application is not None
        assert action_actor is not None
        authorized_role = require_related_role_application_action(
            action_db,
            current_user=action_actor,
            related_role_id=related_role_id,
            application=action_application,
        )
        assert int(authorized_role.id) == related_role_id

        worker_db.execute(text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(DBAPIError):
            worker_db.execute(
                text("SELECT id FROM roles WHERE id = :role_id FOR UPDATE"),
                {"role_id": related_role_id},
            )
    finally:
        action_db.rollback()
        worker_db.rollback()
        action_db.close()
        worker_db.close()


def test_source_action_locks_application_before_owner_role(
    postgres_runtime_engine: Engine,
) -> None:
    """A source-roster mutation waits on its application before its role."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-source-lock-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        actor = User(
            email=f"{prefix}-owner@example.test",
            hashed_password="x",
            is_active=True,
            is_verified=True,
            organization_id=int(application.organization_id),
            role="owner",
        )
        seed_db.add(actor)
        seed_db.flush()
        application_id = int(application.id)
        owner_role_id = int(application.role_id)
        actor_id = int(actor.id)

    blocker_db = session_factory()
    action_db = session_factory()
    try:
        blocker_db.execute(
            text(
                "SELECT id FROM candidate_applications "
                "WHERE id = :application_id FOR UPDATE"
            ),
            {"application_id": application_id},
        )
        action_actor = action_db.get(User, actor_id)
        assert action_actor is not None
        action_db.execute(text("SET LOCAL lock_timeout = '150ms'"))

        with pytest.raises(DBAPIError):
            require_application_job_permission(
                action_db,
                current_user=action_actor,
                application_id=application_id,
                permission=JobPermission.EDIT_ROLE,
            )

        # The timed-out action never reached the canonical role lock.
        locked_role = (
            blocker_db.query(Role)
            .filter(Role.id == owner_role_id)
            .with_for_update(nowait=True, of=Role)
            .one()
        )
        assert int(locked_role.id) == owner_role_id

        action_db.rollback()
        blocker_db.rollback()
        action_actor = action_db.get(User, actor_id)
        assert action_actor is not None
        authorized = require_application_job_permission(
            action_db,
            current_user=action_actor,
            application_id=application_id,
            permission=JobPermission.EDIT_ROLE,
        )
        assert int(authorized.id) == application_id

        blocker_db.execute(text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(DBAPIError):
            blocker_db.execute(
                text("SELECT id FROM roles WHERE id = :role_id FOR UPDATE"),
                {"role_id": owner_role_id},
            )
    finally:
        action_db.rollback()
        blocker_db.rollback()
        action_db.close()
        blocker_db.close()


def test_source_action_refreshes_stale_identity_state_after_concurrent_commit(
    postgres_runtime_engine: Engine,
) -> None:
    """Authorization observes a committed close despite an identity-map hit."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-source-refresh-{uuid4().hex}"
    with session_factory.begin() as seed_db:
        application = _seed_application(seed_db, prefix=prefix)
        actor = User(
            email=f"{prefix}-owner@example.test",
            hashed_password="x",
            is_active=True,
            is_verified=True,
            organization_id=int(application.organization_id),
            role="owner",
        )
        seed_db.add(actor)
        seed_db.flush()
        application_id = int(application.id)
        actor_id = int(actor.id)

    stale_db = session_factory()
    writer_db = session_factory()
    try:
        stale_application = stale_db.get(CandidateApplication, application_id)
        stale_actor = stale_db.get(User, actor_id)
        assert stale_application is not None
        assert stale_actor is not None
        assert stale_application.application_outcome == "open"

        writer_db.execute(
            text(
                "UPDATE candidate_applications "
                "SET application_outcome = 'withdrawn' WHERE id = :application_id"
            ),
            {"application_id": application_id},
        )
        writer_db.commit()
        assert stale_application.application_outcome == "open"

        authorized = require_application_job_permission(
            stale_db,
            current_user=stale_actor,
            application_id=application_id,
            permission=JobPermission.EDIT_ROLE,
        )

        assert authorized is stale_application
        assert authorized.application_outcome == "withdrawn"
    finally:
        stale_db.rollback()
        writer_db.rollback()
        stale_db.close()
        writer_db.close()


def test_postgres_executes_candidate_json_array_filters(postgres_db: Session) -> None:
    """Execute the JSONB containment/array expansion that SQLite only compiles."""

    application = _seed_application(
        postgres_db,
        prefix=f"pg-json-{uuid4().hex}",
    )
    parsed = ParsedFilter(
        skills_all=["Python", "AWS"],
        titles_any=["project manager"],
        locations_country=["UK"],
        min_years_experience=5,
    )
    base_query = postgres_db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == int(application.organization_id)
    )

    matched = apply_parsed_filter(base_query, parsed).one()

    assert int(matched.id) == int(application.id)
    assert matched.candidate.skills == ["Python", "Amazon Web Services (AWS)"]
    assert matched.candidate.experience_entries[0]["country"] == "United Kingdom"
    assert (
        apply_parsed_filter(
            base_query,
            ParsedFilter(skills_all=["Rust"]),
        ).count()
        == 0
    )


def test_postgres_enforces_event_uniqueness_and_append_only_trigger(
    postgres_db: Session,
) -> None:
    application = _seed_application(
        postgres_db,
        prefix=f"pg-event-{uuid4().hex}",
    )
    event = CandidateApplicationEvent(
        application_id=int(application.id),
        organization_id=int(application.organization_id),
        event_type="stage_changed",
        from_stage="applied",
        to_stage="review",
        actor_type="system",
        idempotency_key="same-transition",
    )
    postgres_db.add(event)
    postgres_db.flush()

    with pytest.raises(IntegrityError) as duplicate_error:
        with postgres_db.begin_nested():
            postgres_db.add(
                CandidateApplicationEvent(
                    application_id=int(application.id),
                    organization_id=int(application.organization_id),
                    event_type="stage_changed",
                    actor_type="system",
                    idempotency_key="same-transition",
                )
            )
            postgres_db.flush()
    assert "uq_application_event_idempotency_key" in str(duplicate_error.value)

    with pytest.raises(DBAPIError) as update_error:
        with postgres_db.begin_nested():
            postgres_db.execute(
                text(
                    "UPDATE candidate_application_events "
                    "SET event_type = 'rewritten' WHERE id = :event_id"
                ),
                {"event_id": int(event.id)},
            )
    assert "append-only" in str(update_error.value).lower()

    # Cascade cleanup remains possible: the invariant rejects UPDATE, not DELETE.
    deleted = postgres_db.execute(
        text("DELETE FROM candidate_application_events WHERE id = :event_id"),
        {"event_id": int(event.id)},
    )
    assert deleted.rowcount == 1


def test_postgres_transaction_advisory_lock_serializes_provider_scope(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    owner = session_factory()
    contender = session_factory()
    scope = f"provider-contract-{uuid4().hex}"
    entity_id = 41_001
    try:
        serialize_provider_work(owner, scope=scope, entity_id=entity_id)

        same_scope_available = contender.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"),
            {"scope": scope, "entity_id": entity_id},
        ).scalar_one()
        other_entity_available = contender.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"),
            {"scope": scope, "entity_id": entity_id + 1},
        ).scalar_one()
        assert same_scope_available is False
        assert other_entity_available is True

        contender.rollback()
        owner.rollback()
        assert (
            contender.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"),
                {"scope": scope, "entity_id": entity_id},
            ).scalar_one()
            is True
        )
    finally:
        owner.rollback()
        contender.rollback()
        owner.close()
        contender.close()


def test_postgres_known_accepted_batch_recovery_has_one_anchor_owner(
    postgres_runtime_engine: Engine,
) -> None:
    """Two pollers converge on one atomic synthetic-to-provider re-key."""
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    digest = uuid4().hex * 2
    claim_batch_id = f"claim:cv_parse:{digest}"
    provider_batch_id = f"msgbatch_pg_recovery_{uuid4().hex}"
    attempt_id = f"attempt-{uuid4().hex}"
    application_id = int(uuid4().int % 2_000_000_000) + 1
    with session_factory.begin() as seed:
        organization = Organization(
            name=f"PG batch recovery {uuid4().hex}",
            slug=f"pg-batch-recovery-{uuid4().hex}",
        )
        seed.add(organization)
        seed.flush()
        row = AnthropicBatchJob(
            batch_id=claim_batch_id,
            organization_id=int(organization.id),
            feature="cv_parse",
            model="claude-haiku-4-5",
            request_count=1,
            status="submission_ambiguous",
            context={
                f"cvparse-{application_id}": {
                    "organization_id": int(organization.id),
                    "entity_id": f"application:{application_id}",
                    "origin": "workable_autonomous",
                },
                "_submission_claim": {
                    "version": 2,
                    "state": "provider_accepted_anchor_finalize_failed",
                    "claim_batch_id": claim_batch_id,
                    "request_sha256": digest,
                    "request_count": 1,
                    "attempt": 1,
                    "attempt_id": attempt_id,
                    "provider_batch_id": provider_batch_id,
                },
            },
        )
        seed.add(row)
        seed.flush()
        row_id = int(row.id)
        organization_id = int(organization.id)

    both_scanned = Barrier(2)
    original_recover = anthropic_batch_recovery._recover_known_accepted_claim

    def _recover_after_both_scans(candidate):
        both_scanned.wait(timeout=5)
        return original_recover(candidate)

    def _run_recovery() -> dict[str, int]:
        return anthropic_batch_recovery.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )

    try:
        with (
            patch.object(
                anthropic_batch_recovery,
                "SessionLocal",
                session_factory,
            ),
            patch.object(
                anthropic_batch_recovery,
                "_recover_known_accepted_claim",
                _recover_after_both_scans,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _index: _run_recovery(), range(2)))

        assert sum(result["recovered"] for result in results) == 1
        assert sum(result["already_owned"] for result in results) == 1
        assert sum(result["collisions"] for result in results) == 0
        assert sum(result["errors"] for result in results) == 0
        with session_factory() as check:
            anchors = (
                check.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.id == row_id)
                .all()
            )
            assert len(anchors) == 1
            assert anchors[0].batch_id == provider_batch_id
            assert anchors[0].status == "submitted"
            assert anchors[0].context["_submission_claim"]["state"] == "submitted"
            assert (
                check.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.batch_id == claim_batch_id)
                .count()
                == 0
            )
            check.rollback()
    finally:
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(
                delete(AnthropicBatchJob).where(AnthropicBatchJob.id == row_id)
            )
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_postgres_concurrent_strict_batch_pollers_meter_once_or_fail_closed(
    postgres_runtime_engine: Engine,
) -> None:
    """Row locks dedupe valid pollers and serialize invalid pending evidence."""
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    valid_batch_id = f"msgbatch_pg_valid_{uuid4().hex}"
    invalid_batch_id = f"msgbatch_pg_invalid_{uuid4().hex}"
    mixed_batch_id = f"msgbatch_pg_mixed_{uuid4().hex}"
    model = "claude-haiku-4-5"
    with session_factory.begin() as seed:
        organization = Organization(
            name=f"PG batch metering {uuid4().hex}",
            slug=f"pg-batch-metering-{uuid4().hex}",
        )
        seed.add(organization)
        seed.flush()
        organization_id = int(organization.id)

        def _context(batch_id: str, custom_ids: tuple[str, ...]) -> dict:
            claim_id = f"claim:cv_parse:{uuid4().hex}"
            return {
                **{
                    custom_id: {
                        "organization_id": organization_id,
                        "entity_id": (
                            f"application:{custom_id.removeprefix('cvparse-')}"
                        ),
                    }
                    for custom_id in custom_ids
                },
                "_submission_claim": {
                    "version": 2,
                    "state": "submitted",
                    "claim_batch_id": claim_id,
                    "request_sha256": claim_id.removeprefix("claim:cv_parse:"),
                    "request_count": len(custom_ids),
                    "attempt": 1,
                    "attempt_id": uuid4().hex,
                    "provider_batch_id": batch_id,
                },
            }

        for batch_id, custom_ids in (
            (valid_batch_id, ("cvparse-81001", "cvparse-81002")),
            (invalid_batch_id, ("cvparse-82001", "cvparse-82002")),
            (mixed_batch_id, ("cvparse-83001", "cvparse-83002")),
        ):
            seed.add(
                AnthropicBatchJob(
                    batch_id=batch_id,
                    organization_id=organization_id,
                    feature="cv_parse",
                    model=model,
                    request_count=2,
                    status="submitted",
                    context=_context(batch_id, custom_ids),
                )
            )

    messages = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=SimpleNamespace()),
        organization_id=organization_id,
    ).messages
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    def _entry(custom_id: str, *, succeeded: bool, message_id: str):
        message = (
            SimpleNamespace(usage=usage, model=model, id=message_id)
            if succeeded
            else None
        )
        return SimpleNamespace(
            custom_id=custom_id,
            result=SimpleNamespace(
                type="succeeded" if succeeded else "errored",
                message=message,
            ),
        )

    valid_entries = [
        _entry("cvparse-81001", succeeded=True, message_id="msg_pg_valid"),
        _entry("cvparse-81002", succeeded=False, message_id="unused"),
    ]
    invalid_entries = [
        _entry("cvparse-82001", succeeded=True, message_id="msg_pg_invalid"),
        _entry("cvparse-82001", succeeded=False, message_id="unused"),
    ]
    mixed_valid_entries = [
        _entry("cvparse-83001", succeeded=True, message_id="msg_pg_mixed"),
        _entry("cvparse-83002", succeeded=False, message_id="unused"),
    ]
    mixed_invalid_entries = [
        _entry("cvparse-83001", succeeded=False, message_id="unused"),
        _entry("cvparse-83002", succeeded=True, message_id="msg_pg_mixed_bad"),
    ]

    def _run_together(batch_id: str, entries: list) -> None:
        both_started = Barrier(2)

        def _poll(_index: int) -> None:
            both_started.wait(timeout=5)
            anthropic_batch_result_metering.meter_batch_results_safe(
                messages,
                batch_id=batch_id,
                entries=entries,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(_poll, range(2)))

    def _run_validated_poller_against_pending_poller() -> None:
        valid_prepared = Event()
        invalid_recorded = Event()
        original_prepare = anthropic_batch_result_metering._prepare_anchor

        def _prepare_in_race(*, batch_id: str, entries: list):
            if batch_id != mixed_batch_id:
                return original_prepare(batch_id=batch_id, entries=entries)
            if entries is mixed_invalid_entries:
                assert valid_prepared.wait(timeout=5)
                result = original_prepare(batch_id=batch_id, entries=entries)
                invalid_recorded.set()
                return result
            result = original_prepare(batch_id=batch_id, entries=entries)
            valid_prepared.set()
            assert invalid_recorded.wait(timeout=5)
            return result

        with (
            patch.object(
                anthropic_batch_result_metering,
                "_prepare_anchor",
                _prepare_in_race,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(
                    anthropic_batch_result_metering.meter_batch_results_safe,
                    messages,
                    batch_id=mixed_batch_id,
                    entries=entries,
                )
                for entries in (mixed_valid_entries, mixed_invalid_entries)
            ]
            for future in futures:
                future.result(timeout=10)

    try:
        with patch.object(
            anthropic_batch_result_metering,
            "SessionLocal",
            session_factory,
        ):
            _run_together(valid_batch_id, valid_entries)
            _run_together(invalid_batch_id, invalid_entries)
            _run_validated_poller_against_pending_poller()

        with session_factory() as check:
            valid = (
                check.query(AnthropicBatchJob).filter_by(batch_id=valid_batch_id).one()
            )
            invalid = (
                check.query(AnthropicBatchJob)
                .filter_by(batch_id=invalid_batch_id)
                .one()
            )
            mixed = (
                check.query(AnthropicBatchJob).filter_by(batch_id=mixed_batch_id).one()
            )
            assert valid.status == "ended"
            assert valid.metered_at is not None
            assert valid.metered_count == 1
            valid_receipts = {
                receipt.custom_id
                for receipt in check.query(AnthropicBatchResultReceipt)
                .filter_by(batch_job_id=int(valid.id))
                .all()
            }
            assert valid_receipts == {
                "cvparse-81001",
                "cvparse-81002",
            }
            assert "_metered_results" not in valid.context
            assert invalid.status == "submitted"
            assert invalid.metered_at is None
            assert "_metered_results" not in invalid.context
            pending = invalid.context["_result_attribution_validation"]
            assert pending["state"] == "reconciliation_pending"
            assert "duplicate_result_custom_ids" in pending["issues"]
            assert pending["observation_count"] == 2
            assert mixed.status == "submitted"
            assert mixed.metered_at is None
            assert "_metered_results" not in mixed.context
            mixed_pending = mixed.context["_result_attribution_validation"]
            assert mixed_pending["state"] == "reconciliation_pending"
            assert mixed_pending["observation_count"] == 1
            assert "result_outcome_mismatch" in mixed_pending["issues"]
            assert (
                check.query(UsageEvent)
                .filter_by(organization_id=organization_id)
                .count()
                == 1
            )
            assert (
                check.query(ClaudeCallLog)
                .filter_by(organization_id=organization_id)
                .count()
                == 1
            )
            check.rollback()
    finally:
        with postgres_runtime_engine.begin() as cleanup:
            # Production receipts are append-only. This disposable runtime
            # database is owned by the test, so temporarily disable the exact
            # immutability trigger while removing this test's fixtures.
            cleanup.execute(
                text(
                    "ALTER TABLE anthropic_batch_result_receipts DISABLE TRIGGER "
                    "trg_anthropic_batch_receipt_immutable"
                )
            )
            cleanup.execute(
                delete(AnthropicBatchResultReceipt).where(
                    AnthropicBatchResultReceipt.batch_job_id.in_(
                        select(AnthropicBatchJob.id).where(
                            AnthropicBatchJob.batch_id.in_(
                                (
                                    valid_batch_id,
                                    invalid_batch_id,
                                    mixed_batch_id,
                                )
                            )
                        )
                    )
                )
            )
            cleanup.execute(
                text(
                    "ALTER TABLE anthropic_batch_result_receipts ENABLE TRIGGER "
                    "trg_anthropic_batch_receipt_immutable"
                )
            )
            cleanup.execute(
                delete(ClaudeCallLog).where(
                    ClaudeCallLog.organization_id == organization_id
                )
            )
            cleanup.execute(
                delete(UsageEvent).where(UsageEvent.organization_id == organization_id)
            )
            cleanup.execute(
                delete(AnthropicBatchJob).where(
                    AnthropicBatchJob.batch_id.in_(
                        (valid_batch_id, invalid_batch_id, mixed_batch_id)
                    )
                )
            )
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_task_repository_mutex_survives_caller_rollback_and_releases(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    caller = session_factory()
    contender = session_factory()
    task_id = 42_101
    parameters = {
        "scope": TASK_REPOSITORY_WRITE_LOCK_SCOPE,
        "task_id": task_id,
    }
    try:
        with task_repository_write_mutex(caller, task_id=task_id):
            caller.execute(text("SELECT 1"))
            caller.rollback()
            assert (
                contender.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"),
                    parameters,
                ).scalar_one()
                is False
            )

        assert (
            contender.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"),
                parameters,
            ).scalar_one()
            is True
        )
        assert (
            contender.execute(
                text("SELECT pg_advisory_unlock(hashtext(:scope), :task_id)"),
                parameters,
            ).scalar_one()
            is True
        )
    finally:
        caller.rollback()
        contender.execute(text("SELECT pg_advisory_unlock_all()"))
        contender.rollback()
        caller.close()
        contender.close()


def test_assessment_workspace_mutex_releases_orm_rows_during_provider_boundary(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"assessment-workspace-boundary-{uuid4().hex}"
    with session_factory.begin() as seed:
        organization = Organization(name=prefix, slug=prefix)
        seed.add(organization)
        seed.flush()
        task = Task(
            organization_id=int(organization.id),
            name="Assessment workspace boundary task",
            task_key=prefix,
            is_active=True,
        )
        seed.add(task)
        seed.flush()
        assessment = Assessment(
            organization_id=int(organization.id),
            task_id=int(task.id),
            token=prefix,
        )
        seed.add(assessment)
        seed.flush()
        organization_id = int(organization.id)
        task_id = int(task.id)
        assessment_id = int(assessment.id)

    caller = session_factory()
    contender = session_factory()
    parameters = {
        "scope": ASSESSMENT_WORKSPACE_LOCK_SCOPE,
        "assessment_id": assessment_id,
    }
    try:
        with assessment_workspace_mutex(caller, assessment_id=assessment_id):
            caller.query(Assessment).filter(
                Assessment.id == assessment_id
            ).with_for_update().one()
            caller.query(Task).filter(Task.id == task_id).with_for_update().one()
            caller.rollback()

            assert caller.in_transaction() is False
            assert (
                contender.execute(
                    text(
                        "SELECT pg_try_advisory_lock(hashtext(:scope), :assessment_id)"
                    ),
                    parameters,
                ).scalar_one()
                is False
            )
            assert (
                int(
                    contender.query(Assessment)
                    .filter(Assessment.id == assessment_id)
                    .with_for_update(nowait=True)
                    .one()
                    .id
                )
                == assessment_id
            )
            assert (
                int(
                    contender.query(Task)
                    .filter(Task.id == task_id)
                    .with_for_update(nowait=True)
                    .one()
                    .id
                )
                == task_id
            )
            contender.rollback()

        assert (
            contender.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:scope), :assessment_id)"),
                parameters,
            ).scalar_one()
            is True
        )
        assert (
            contender.execute(
                text("SELECT pg_advisory_unlock(hashtext(:scope), :assessment_id)"),
                parameters,
            ).scalar_one()
            is True
        )
    finally:
        caller.rollback()
        contender.execute(text("SELECT pg_advisory_unlock_all()"))
        contender.rollback()
        caller.close()
        contender.close()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(delete(Assessment).where(Assessment.id == assessment_id))
            cleanup.execute(delete(Task).where(Task.id == task_id))
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_assessment_workspace_mutex_waiters_do_not_exhaust_a_small_pool(
    postgres_runtime_engine: Engine,
) -> None:
    """Blocked logical waiters release the one slot the owner needs for ORM work."""

    small_engine = create_engine(
        postgres_runtime_engine.url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
    )
    register_workspace_lock_engine_factory(
        small_engine,
        lambda: create_engine(
            postgres_runtime_engine.url,
            pool_size=5,
            max_overflow=0,
            pool_timeout=1,
        ),
    )
    session_factory = sessionmaker(bind=small_engine, expire_on_commit=False)
    assessment_id = int(uuid4().int % 2_000_000_000) + 1
    owner_has_lock = Event()
    all_waiters_prepared = Event()
    prepared_guard = Lock()
    prepared_waiters = 0
    acquired = []
    application_checkouts = 0

    @event.listens_for(small_engine, "checkout")
    def _observe_application_checkout(*_args) -> None:
        nonlocal application_checkouts
        application_checkouts += 1

    def _waiter() -> None:
        nonlocal prepared_waiters
        with session_factory() as waiter_db:
            assert waiter_db.execute(text("SELECT 1")).scalar_one() == 1
            assert waiter_db.in_transaction() is True
            prepare_assessment_workspace_mutex(waiter_db)
            assert waiter_db.in_transaction() is False
            with prepared_guard:
                prepared_waiters += 1
                if prepared_waiters == 4:
                    all_waiters_prepared.set()
            with assessment_workspace_mutex(
                waiter_db,
                assessment_id=assessment_id,
            ):
                acquired.append(True)

    executor = ThreadPoolExecutor(max_workers=4)
    futures = []
    owner_db = session_factory()
    try:
        with assessment_workspace_mutex(
            owner_db,
            assessment_id=assessment_id,
        ):
            owner_has_lock.set()
            futures = [executor.submit(_waiter) for _ in range(4)]
            assert all_waiters_prepared.wait(timeout=5)

            # Every waiter has checked out and returned a real application
            # connection before competing for the held lock. Subsequent wait
            # attempts use only the dedicated engine, leaving the normal pool
            # completely available to finalize provider work.
            assert application_checkouts == 4
            assert small_engine.pool.checkedout() == 0
            assert owner_db.execute(text("SELECT 1")).scalar_one() == 1
            assert application_checkouts == 5
            owner_db.rollback()

        for future in futures:
            future.result(timeout=10)
        assert acquired == [True, True, True, True]
    finally:
        owner_has_lock.clear()
        owner_db.close()
        executor.shutdown(wait=True, cancel_futures=True)
        unregister_workspace_lock_engine_factory(small_engine)
        small_engine.dispose()


def test_graph_manifest_pair_and_postgres_trigger_are_immutable(
    postgres_db: Session,
) -> None:
    operation_id = str(uuid4())
    row = GraphIngestDispatch(
        operation_id=operation_id,
        organization_id=None,
        work_kind="candidate",
        entity_id=91,
        source_refs=[{"kind": "candidate", "id": 91}],
        operation_manifest=None,
        operation_manifest_sha256=None,
    )
    postgres_db.add(row)
    postgres_db.flush()
    stored_nulls = postgres_db.execute(
        text(
            "SELECT operation_manifest IS NULL, "
            "operation_manifest_sha256 IS NULL "
            "FROM graph_ingest_dispatches WHERE operation_id = :operation_id"
        ),
        {"operation_id": operation_id},
    ).one()
    assert stored_nulls == (True, True)

    with pytest.raises(IntegrityError):
        with postgres_db.begin_nested():
            postgres_db.execute(
                text(
                    "UPDATE graph_ingest_dispatches "
                    "SET operation_manifest = CAST(:manifest AS json) "
                    "WHERE operation_id = :operation_id"
                ),
                {"operation_id": operation_id, "manifest": '{"version": 1}'},
            )
            postgres_db.flush()

    manifest = {
        "version": 1,
        "work_kind": "candidate",
        "entity_id": 91,
        "episode_count": 1,
        "episodes": [
            {
                "ordinal": 0,
                "episode_name": "candidate-91-profile",
                "episode_sha256": "a" * 64,
            }
        ],
    }
    digest = manifest_sha256(manifest)
    postgres_db.execute(
        text(
            "UPDATE graph_ingest_dispatches "
            "SET operation_manifest = CAST(:manifest AS json), "
            "operation_manifest_sha256 = :digest "
            "WHERE operation_id = :operation_id"
        ),
        {
            "operation_id": operation_id,
            "manifest": json.dumps(manifest),
            "digest": digest,
        },
    )
    postgres_db.flush()

    for mutation in (
        "operation_manifest_sha256 = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
        "operation_manifest = CAST('{\"tampered\": true}' AS json)",
    ):
        with pytest.raises(DBAPIError, match="manifest is immutable"):
            with postgres_db.begin_nested():
                postgres_db.execute(
                    text(
                        "UPDATE graph_ingest_dispatches SET "
                        f"{mutation} WHERE operation_id = :operation_id"
                    ),
                    {"operation_id": operation_id},
                )
                postgres_db.flush()

    assert (
        postgres_db.execute(
            text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname = 'prevent_graph_ingest_manifest_mutation_v187'"
            )
        ).scalar_one()
        == 1
    )
    assert (
        postgres_db.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'trg_graph_ingest_manifest_immutable' "
                "AND NOT tgisinternal"
            )
        ).scalar_one()
        == 1
    )


def test_nightly_fit_serializes_spend_without_locking_organization_row(
    postgres_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"policy-fit-boundary-{uuid4().hex}"
    with session_factory.begin() as seed:
        organization = Organization(name=prefix, slug=prefix)
        seed.add(organization)
        seed.flush()
        organization_id = int(organization.id)

    caller = session_factory()
    contender = session_factory()
    examples = [
        TrainingExample(
            features={"role_fit_score": float(index)},
            label=1.0 if index >= 30 else 0.0,
            weight=1.0,
        )
        for index in range(60)
    ]
    monkeypatch.setattr(
        nightly_policy_fit,
        "_collect_training_data",
        lambda *args, **kwargs: list(examples),
    )
    observed = []

    def provider_boundary(*_args, **_kwargs):
        assert not caller.in_transaction()
        advisory_available = contender.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:scope), :organization_id)"),
            {
                "scope": POLICY_FIT_LOCK_SCOPE,
                "organization_id": organization_id,
            },
        ).scalar_one()
        # The duplicate-spend mutex is held, but the ordinary org row remains
        # NOWAIT-lockable throughout the model/provider callback.
        locked_org = (
            contender.query(Organization)
            .filter(Organization.id == organization_id)
            .with_for_update(nowait=True)
            .one()
        )
        observed.append((advisory_available, int(locked_org.id)))
        contender.rollback()
        return FittedModel(coefs={"role_fit_score": 0.1}), {"loss": 0.2}

    monkeypatch.setattr(
        nightly_policy_fit,
        "_fit_candidate_model",
        provider_boundary,
    )
    try:
        result = nightly_policy_fit.fit_for_org(
            caller,
            organization_id=organization_id,
            since=datetime.now(timezone.utc) - timedelta(days=90),
            role_id=None,
        )

        assert result is not None
        assert observed == [(False, organization_id)]
        assert (
            contender.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:scope), :organization_id)"),
                {
                    "scope": POLICY_FIT_LOCK_SCOPE,
                    "organization_id": organization_id,
                },
            ).scalar_one()
            is True
        )
        assert (
            contender.execute(
                text("SELECT pg_advisory_unlock(hashtext(:scope), :organization_id)"),
                {
                    "scope": POLICY_FIT_LOCK_SCOPE,
                    "organization_id": organization_id,
                },
            ).scalar_one()
            is True
        )
    finally:
        caller.rollback()
        contender.execute(text("SELECT pg_advisory_unlock_all()"))
        contender.rollback()
        caller.close()
        contender.close()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(
                delete(PolicyVersion).where(
                    PolicyVersion.organization_id == organization_id
                )
            )
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_task_repository_mutex_unlocks_when_caller_raises(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    caller = session_factory()
    contender = session_factory()
    task_id = 42_102
    parameters = {
        "scope": TASK_REPOSITORY_WRITE_LOCK_SCOPE,
        "task_id": task_id,
    }
    try:
        with pytest.raises(RuntimeError, match="remote sync failed"):
            with task_repository_write_mutex(caller, task_id=task_id):
                raise RuntimeError("remote sync failed")

        assert (
            contender.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"),
                parameters,
            ).scalar_one()
            is True
        )
        assert (
            contender.execute(
                text("SELECT pg_advisory_unlock(hashtext(:scope), :task_id)"),
                parameters,
            ).scalar_one()
            is True
        )
    finally:
        caller.rollback()
        contender.execute(text("SELECT pg_advisory_unlock_all()"))
        contender.rollback()
        caller.close()
        contender.close()


def test_task_repository_mutex_nonblocking_mode_reports_busy(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    owner = session_factory()
    contender = session_factory()
    task_id = 42_103
    try:
        with task_repository_write_mutex(owner, task_id=task_id):
            with pytest.raises(TaskRepositoryBusyError):
                with task_repository_write_mutex(
                    contender,
                    task_id=task_id,
                    wait=False,
                ):
                    pytest.fail("busy task repository mutex was acquired")

        with task_repository_write_mutex(
            contender,
            task_id=task_id,
            wait=False,
        ):
            pass
    finally:
        owner.rollback()
        contender.rollback()
        owner.close()
        contender.close()


@pytest.mark.parametrize("mutation", ["inactive", "unlinked"])
def test_assessment_branch_locks_task_authority_through_caller_commit(
    postgres_runtime_engine: Engine,
    mutation: str,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"branch-authority-{mutation}-{uuid4().hex}"
    with session_factory.begin() as seed:
        organization = Organization(name=prefix, slug=prefix)
        seed.add(organization)
        seed.flush()
        role = Role(
            organization_id=int(organization.id),
            name="Branch authority role",
            source="manual",
        )
        task = Task(
            organization_id=int(organization.id),
            name="Branch authority task",
            task_key=prefix,
            is_active=True,
        )
        seed.add_all([role, task])
        seed.flush()
        seed.execute(
            role_tasks.insert().values(role_id=int(role.id), task_id=int(task.id))
        )
        assessment = Assessment(
            organization_id=int(organization.id),
            role_id=int(role.id),
            task_id=int(task.id),
            token=prefix,
        )
        seed.add(assessment)
        seed.flush()
        organization_id = int(organization.id)
        role_id = int(role.id)
        task_id = int(task.id)
        assessment_id = int(assessment.id)

    provider_entered = Event()
    release_provider = Event()
    helper_returned = Event()
    allow_caller_commit = Event()
    mutation_started = Event()
    mutation_committed = Event()

    class BlockingRepository:
        def create_assessment_branch(self, snapshot, current_assessment_id):
            assert int(snapshot.id) == task_id
            assert int(current_assessment_id) == assessment_id
            provider_entered.set()
            assert release_provider.wait(timeout=5)
            return SimpleNamespace(
                repo_url="https://example.test/assessment.git",
                branch_name=f"assessment/{assessment_id}",
                clone_command="git clone example.test/assessment.git",
            )

    def _provision() -> None:
        with session_factory() as caller:
            current = caller.get(Assessment, assessment_id)
            assert current is not None
            create_serialized_assessment_branch(
                caller,
                BlockingRepository(),
                current,
            )
            helper_returned.set()
            assert allow_caller_commit.wait(timeout=5)
            caller.commit()

    def _mutate_authority() -> None:
        with session_factory() as writer:
            mutation_started.set()
            if mutation == "inactive":
                current = writer.get(Task, task_id)
                assert current is not None
                current.is_active = False
                writer.flush()
            else:
                writer.execute(
                    role_tasks.delete().where(
                        role_tasks.c.role_id == role_id,
                        role_tasks.c.task_id == task_id,
                    )
                )
            writer.commit()
            mutation_committed.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            provision_future = executor.submit(_provision)
            assert provider_entered.wait(timeout=5)
            mutation_future = executor.submit(_mutate_authority)
            assert mutation_started.wait(timeout=5)
            assert mutation_committed.wait(timeout=0.25) is False

            release_provider.set()
            assert helper_returned.wait(timeout=5)
            assert mutation_committed.wait(timeout=0.25) is False

            allow_caller_commit.set()
            provision_future.result(timeout=5)
            mutation_future.result(timeout=5)
            assert mutation_committed.is_set()
    finally:
        release_provider.set()
        allow_caller_commit.set()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(delete(Assessment).where(Assessment.id == assessment_id))
            cleanup.execute(role_tasks.delete().where(role_tasks.c.role_id == role_id))
            cleanup.execute(delete(Task).where(Task.id == task_id))
            cleanup.execute(delete(Role).where(Role.id == role_id))
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_postgres_template_catalog_sync_is_serialized_across_workers(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    owner = session_factory()
    contender = session_factory()
    try:
        serialize_task_catalog_sync(owner)

        assert (
            contender.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), 0)"),
                {"scope": TASK_CATALOG_SYNC_LOCK_SCOPE},
            ).scalar_one()
            is False
        )

        contender.rollback()
        owner.rollback()
        assert (
            contender.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), 0)"),
                {"scope": TASK_CATALOG_SYNC_LOCK_SCOPE},
            ).scalar_one()
            is True
        )
    finally:
        owner.rollback()
        contender.rollback()
        owner.close()
        contender.close()


def test_postgres_catalog_rechecks_role_reference_after_waiting_for_task_lock(
    postgres_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link committed while sync waits prevents stale deactivation."""

    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-catalog-link-{uuid4().hex}"
    with session_factory.begin() as seed:
        organization = Organization(name=prefix, slug=prefix)
        seed.add(organization)
        seed.flush()
        role = Role(
            organization_id=int(organization.id),
            name="Catalogue link role",
        )
        task = Task(
            organization_id=None,
            name="Catalogue template",
            task_key=prefix,
            is_template=True,
            is_active=True,
        )
        seed.add_all([role, task])
        seed.flush()
        organization_id = int(organization.id)
        role_id = int(role.id)
        task_id = int(task.id)

    link_db = session_factory()
    lock_attempted = Event()
    original_lock = task_catalog._lock_existing_template

    def _signal_target_lock(db, *, task_id: int, task_key: str):
        if task_id == int(task_id_for_signal):
            lock_attempted.set()
        return original_lock(db, task_id=task_id, task_key=task_key)

    task_id_for_signal = task_id
    monkeypatch.setattr(
        task_catalog,
        "_lock_existing_template",
        _signal_target_lock,
    )
    try:
        link_db.query(Role).filter(Role.id == role_id).with_for_update(of=Role).one()
        link_db.query(Task).filter(Task.id == task_id).with_for_update(of=Task).one()
        link_db.execute(role_tasks.insert().values(role_id=role_id, task_id=task_id))

        def _run_sync() -> dict[str, int]:
            with session_factory() as sync_db:
                return sync_template_task_specs(sync_db, [])

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_sync)
            reached_lock = lock_attempted.wait(timeout=3)
            if not reached_lock:
                link_db.rollback()
                future.result(timeout=5)
            assert reached_lock
            assert future.done() is False
            link_db.commit()
            stats = future.result(timeout=5)

        assert stats["preserved_referenced"] == 1
        assert stats["deactivated"] == 0
        with session_factory() as verify:
            assert verify.get(Task, task_id).is_active is True
            assert (
                verify.execute(
                    role_tasks.select().where(
                        role_tasks.c.role_id == role_id,
                        role_tasks.c.task_id == task_id,
                    )
                ).first()
                is not None
            )
    finally:
        link_db.rollback()
        link_db.close()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(role_tasks.delete().where(role_tasks.c.task_id == task_id))
            cleanup.execute(delete(Task).where(Task.id == task_id))
            cleanup.execute(delete(Role).where(Role.id == role_id))
            cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )


def test_postgres_skip_locked_outbox_claims_are_disjoint(
    postgres_runtime_engine: Engine,
) -> None:
    session_factory = sessionmaker(
        bind=postgres_runtime_engine,
        expire_on_commit=False,
    )
    prefix = f"pg-claim-{uuid4().hex}"
    with session_factory.begin() as seed:
        rows = [
            BrainFeedOutbox(
                record_kind="decision",
                event_id=f"{prefix}-{index}",
                payload={"sequence": index, "nested": ["safe", index]},
                status=BRAIN_FEED_STATUS_PENDING,
                attempts=0,
            )
            for index in (1, 2)
        ]
        seed.add_all(rows)
        seed.flush()
        first_id, second_id = (int(row.id) for row in rows)

    lock_holder = session_factory()
    first_claimer = session_factory()
    second_claimer = session_factory()
    try:
        locked = (
            lock_holder.query(BrainFeedOutbox)
            .filter(BrainFeedOutbox.id == first_id)
            .with_for_update()
            .one()
        )
        assert int(locked.id) == first_id

        first_claim = brain_feed_outbox._claim(first_claimer, batch_size=2)
        assert [int(row.id) for row in first_claim] == [second_id]
        assert first_claim[0].status == BRAIN_FEED_STATUS_PROCESSING
        assert int(first_claim[0].attempts) == 1

        lock_holder.rollback()
        second_claim = brain_feed_outbox._claim(second_claimer, batch_size=2)
        assert [int(row.id) for row in second_claim] == [first_id]
        assert second_claim[0].status == BRAIN_FEED_STATUS_PROCESSING
        assert int(second_claim[0].attempts) == 1
        assert {int(first_claim[0].id), int(second_claim[0].id)} == {
            first_id,
            second_id,
        }
    finally:
        lock_holder.rollback()
        first_claimer.rollback()
        second_claimer.rollback()
        lock_holder.close()
        first_claimer.close()
        second_claimer.close()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(
                delete(BrainFeedOutbox).where(
                    BrainFeedOutbox.event_id.like(f"{prefix}-%")
                )
            )
