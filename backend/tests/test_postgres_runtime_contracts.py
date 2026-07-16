"""Bounded contracts for production PostgreSQL semantics SQLite cannot prove.

The normal backend suite intentionally stays on fast, isolated SQLite. These
tests migrate one disposable PostgreSQL database and execute only the small
set of dialect-specific behavior the application relies on directly: JSON
array search, transaction advisory locks, append-only/unique constraints, and
``FOR UPDATE SKIP LOCKED`` outbox claims.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.actions import approve_decision
from app.actions.types import ACTOR_RECRUITER, Actor
from app.brain_feed import outbox as brain_feed_outbox
from app.candidate_search.query_builder_sql import apply_parsed_filter
from app.candidate_search.schemas import ParsedFilter
from app.domains.assessments_runtime.application_mutation_authorization import (
    require_application_job_permission,
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
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.job_hiring_team import TEAM_ROLE_RECRUITER, JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role, role_tasks
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.models.task import Task
from app.models.workspace_pause_migration_audit import WorkspacePauseMigrationAudit
from app.services.ats_operation_guards import lock_live_application_move
from app.services.assessment_repository_operations import (
    create_serialized_assessment_branch,
)
from app.services.auto_reject_op import execute_auto_reject_op
from app.services.bulk_decision_service.stage_toggle import (
    reconcile_pending_positive_decisions,
)
from app.services.provider_usage_admission import serialize_provider_work
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
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "183_preserve_related_role_history"
            yield engine
        finally:
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
            text(
                "SELECT id FROM agent_decisions "
                "WHERE id = :decision_id FOR UPDATE"
            ),
            {"decision_id": decision_id},
        )
        reconcile_db.execute(text("SET LOCAL lock_timeout = '150ms'"))

        assert reconcile_pending_positive_decisions(
            reconcile_db,
            role_id=role_id,
            expected_role_version=role_version,
        ) == 0
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
                assert observer.execute(
                    text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": row_id},
                ).scalar_one() == row_id
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
            text(
                "SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"
            ),
            {"scope": scope, "entity_id": entity_id},
        ).scalar_one()
        other_entity_available = contender.execute(
            text(
                "SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"
            ),
            {"scope": scope, "entity_id": entity_id + 1},
        ).scalar_one()
        assert same_scope_available is False
        assert other_entity_available is True

        contender.rollback()
        owner.rollback()
        assert contender.execute(
            text(
                "SELECT pg_try_advisory_xact_lock(hashtext(:scope), :entity_id)"
            ),
            {"scope": scope, "entity_id": entity_id},
        ).scalar_one() is True
    finally:
        owner.rollback()
        contender.rollback()
        owner.close()
        contender.close()


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
            assert contender.execute(
                text(
                    "SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"
                ),
                parameters,
            ).scalar_one() is False

        assert contender.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"),
            parameters,
        ).scalar_one() is True
        assert contender.execute(
            text("SELECT pg_advisory_unlock(hashtext(:scope), :task_id)"),
            parameters,
        ).scalar_one() is True
    finally:
        caller.rollback()
        contender.execute(text("SELECT pg_advisory_unlock_all()"))
        contender.rollback()
        caller.close()
        contender.close()


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

        assert contender.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"),
            parameters,
        ).scalar_one() is True
        assert contender.execute(
            text("SELECT pg_advisory_unlock(hashtext(:scope), :task_id)"),
            parameters,
        ).scalar_one() is True
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
            cleanup.execute(
                role_tasks.delete().where(role_tasks.c.role_id == role_id)
            )
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

        assert contender.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), 0)"),
            {"scope": TASK_CATALOG_SYNC_LOCK_SCOPE},
        ).scalar_one() is False

        contender.rollback()
        owner.rollback()
        assert contender.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:scope), 0)"),
            {"scope": TASK_CATALOG_SYNC_LOCK_SCOPE},
        ).scalar_one() is True
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
        link_db.execute(
            role_tasks.insert().values(role_id=role_id, task_id=task_id)
        )

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
            assert verify.execute(
                role_tasks.select().where(
                    role_tasks.c.role_id == role_id,
                    role_tasks.c.task_id == task_id,
                )
            ).first() is not None
    finally:
        link_db.rollback()
        link_db.close()
        with postgres_runtime_engine.begin() as cleanup:
            cleanup.execute(
                role_tasks.delete().where(role_tasks.c.task_id == task_id)
            )
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
