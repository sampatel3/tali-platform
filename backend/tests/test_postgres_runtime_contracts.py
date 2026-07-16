"""Bounded contracts for production PostgreSQL semantics SQLite cannot prove.

The normal backend suite intentionally stays on fast, isolated SQLite. These
tests migrate one disposable PostgreSQL database and execute only the small
set of dialect-specific behavior the application relies on directly: JSON
array search, transaction advisory locks, append-only/unique constraints, and
``FOR UPDATE SKIP LOCKED`` outbox claims.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.brain_feed import outbox as brain_feed_outbox
from app.candidate_search.query_builder_sql import apply_parsed_filter
from app.candidate_search.schemas import ParsedFilter
from app.models.brain_feed_outbox import (
    BRAIN_FEED_STATUS_PENDING,
    BRAIN_FEED_STATUS_PROCESSING,
    BrainFeedOutbox,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.services.provider_usage_admission import serialize_provider_work
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
                ).scalar_one() == "179_restore_schema_metadata_invariants"
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
