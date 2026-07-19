"""Assessment workspace mutex re-entry and concurrency contracts."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.domains.assessments_runtime import (
    workspace_serialization as workspace_serialization_module,
)
from app.domains.assessments_runtime.workspace_serialization import (
    assessment_workspace_mutex,
    async_assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)
from app.platform.database import (
    register_workspace_lock_engine_factory,
    unregister_workspace_lock_engine_factory,
)
from app.models.organization import Organization


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakePostgresConnection:
    def __init__(self, engine, try_result):
        self.engine = engine
        self.try_result = try_result
        self.closed = False
        self.invalidated = False
        self.unlock_calls = 0

    def execution_options(self, **_kwargs):
        return self

    def execute(self, statement, _parameters):
        sql = str(statement)
        if "pg_try_advisory_lock" in sql:
            if self.engine.try_started is not None:
                self.engine.try_started.set()
            if self.engine.release_try is not None:
                assert self.engine.release_try.wait(timeout=1)
            if isinstance(self.try_result, Exception):
                raise self.try_result
            return _ScalarResult(self.try_result)
        if "pg_advisory_unlock" in sql:
            self.unlock_calls += 1
            if isinstance(self.engine.unlock_result, Exception):
                raise self.engine.unlock_result
            return _ScalarResult(self.engine.unlock_result)
        raise AssertionError(f"Unexpected SQL: {sql}")

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True


class _FakePostgresEngine:
    def __init__(
        self,
        try_results,
        *,
        unlock_result=True,
        try_started=None,
        release_try=None,
    ):
        self.dialect = SimpleNamespace(name="postgresql")
        self.try_results = list(try_results)
        self.unlock_result = unlock_result
        self.try_started = try_started
        self.release_try = release_try
        self.connections = []

    def connect(self):
        index = len(self.connections)
        try_result = self.try_results[min(index, len(self.try_results) - 1)]
        connection = _FakePostgresConnection(self, try_result)
        self.connections.append(connection)
        return connection


class _FakePostgresSession:
    def __init__(self, engine):
        self.engine = engine

    def get_bind(self):
        return self.engine


def _real_engine(*, pool_size: int = 1):
    return create_engine(
        "sqlite+pysqlite:///:memory:?lock_contract=1",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=0,
    )


def test_real_lock_engine_is_distinct_and_never_checks_out_the_app_pool():
    application_engine = _real_engine(pool_size=1)
    created = []

    def factory():
        lock_engine = _real_engine(pool_size=2)
        created.append(lock_engine)
        return lock_engine

    register_workspace_lock_engine_factory(application_engine, factory)
    app_checkouts = 0

    def counted_checkout(*_args):
        nonlocal app_checkouts
        app_checkouts += 1

    event.listen(application_engine, "checkout", counted_checkout)
    try:
        lock_engine = workspace_serialization_module._postgres_lock_engine(
            application_engine
        )
        assert lock_engine is created[0]
        assert lock_engine is not application_engine
        assert lock_engine.pool is not application_engine.pool
        lock_connections = [lock_engine.connect(), lock_engine.connect()]
        try:
            assert application_engine.pool.checkedout() == 0
            with application_engine.connect() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert app_checkouts == 1
            assert application_engine.pool.checkedout() == 0
            assert lock_engine.pool.checkedout() == 2
        finally:
            for connection in lock_connections:
                connection.close()
    finally:
        workspace_serialization_module._dispose_postgres_lock_engine(
            application_engine
        )
        unregister_workspace_lock_engine_factory(application_engine)
        application_engine.dispose()


def test_registered_factory_preserves_explicit_url_query_connect_args_and_creator():
    application_engine = _real_engine()
    expected_url = "sqlite+pysqlite:///:memory:?contract=query-preserved"
    expected_connect_args = {"check_same_thread": False}
    creator_calls = 0

    def dbapi_creator():
        nonlocal creator_calls
        creator_calls += 1
        return sqlite3.connect(":memory:", check_same_thread=False)

    def factory():
        assert expected_url.endswith("contract=query-preserved")
        assert expected_connect_args == {"check_same_thread": False}
        return create_engine(
            expected_url,
            creator=dbapi_creator,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=0,
        )

    register_workspace_lock_engine_factory(application_engine, factory)
    try:
        lock_engine = workspace_serialization_module._postgres_lock_engine(
            application_engine
        )
        assert lock_engine.url.query["contract"] == "query-preserved"
        with lock_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert creator_calls == 1
    finally:
        workspace_serialization_module._dispose_postgres_lock_engine(
            application_engine
        )
        unregister_workspace_lock_engine_factory(application_engine)
        application_engine.dispose()


def test_lock_engine_is_recreated_after_fork_pid_change(monkeypatch):
    application_engine = _real_engine()
    created = []

    def factory():
        lock_engine = _real_engine()
        created.append(lock_engine)
        return lock_engine

    register_workspace_lock_engine_factory(application_engine, factory)
    monkeypatch.setattr(workspace_serialization_module.os, "getpid", lambda: 101)
    try:
        first = workspace_serialization_module._postgres_lock_engine(
            application_engine
        )
        monkeypatch.setattr(
            workspace_serialization_module.os,
            "getpid",
            lambda: 202,
        )
        second = workspace_serialization_module._postgres_lock_engine(
            application_engine
        )
        assert first is created[0]
        assert second is created[1]
        assert second is not first
        assert second.pool is not first.pool
    finally:
        workspace_serialization_module._dispose_postgres_lock_engine(
            application_engine
        )
        unregister_workspace_lock_engine_factory(application_engine)
        application_engine.dispose()


def test_application_engine_dispose_cleans_up_cached_lock_engine():
    application_engine = _real_engine()
    lock_engine = _real_engine()
    disposed = []
    event.listen(lock_engine, "engine_disposed", lambda _engine: disposed.append(True))
    register_workspace_lock_engine_factory(
        application_engine,
        lambda: lock_engine,
    )
    try:
        assert (
            workspace_serialization_module._postgres_lock_engine(application_engine)
            is lock_engine
        )
        application_engine.dispose()
        assert disposed == [True]
        assert application_engine not in (
            workspace_serialization_module._POSTGRES_LOCK_ENGINES
        )
    finally:
        unregister_workspace_lock_engine_factory(application_engine)
        application_engine.dispose()


def test_mutex_requires_read_transaction_release_and_preserves_pending_writes():
    engine = _real_engine()
    session = Session(engine)
    try:
        session.execute(text("SELECT 1"))
        assert session.in_transaction()
        with pytest.raises(RuntimeError, match="prepare_assessment_workspace_mutex"):
            with assessment_workspace_mutex(session, assessment_id=918_280):
                pass
        prepare_assessment_workspace_mutex(session)
        assert not session.in_transaction()
        with assessment_workspace_mutex(session, assessment_id=918_280):
            pass

        pending = Organization(name="Do not discard pending mutex write")
        session.add(pending)
        with pytest.raises(RuntimeError, match="uncommitted writes"):
            prepare_assessment_workspace_mutex(session)
        assert pending in session.new
        assert session.in_transaction()
        session.rollback()

        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE mutex_write_probe (id INTEGER PRIMARY KEY)")
            )
        session.execute(text("INSERT INTO mutex_write_probe (id) VALUES (1)"))
        session.flush()
        assert not session.new and not session.dirty and not session.deleted
        with pytest.raises(RuntimeError, match="uncommitted writes"):
            prepare_assessment_workspace_mutex(session)
        assert session.execute(
            text("SELECT count(*) FROM mutex_write_probe")
        ).scalar_one() == 1
        session.rollback()
        assert session.execute(
            text("SELECT count(*) FROM mutex_write_probe")
        ).scalar_one() == 0

        session.execute(
            text(
                "WITH candidate(id) AS (VALUES (2)) "
                "INSERT INTO mutex_write_probe (id) SELECT id FROM candidate"
            )
        )
        with pytest.raises(RuntimeError, match="uncommitted writes"):
            prepare_assessment_workspace_mutex(session)
        assert session.execute(
            text("SELECT count(*) FROM mutex_write_probe")
        ).scalar_one() == 1
        session.rollback()
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_text_clause_write_classifier_is_conservative_for_ambiguous_commands():
    classifier = workspace_serialization_module._text_clause_may_write

    assert classifier(text("SELECT 1")) is False
    assert classifier(text("VALUES (1)")) is False
    assert classifier(text("WITH source AS (SELECT 1) SELECT * FROM source")) is True
    assert classifier(text("EXPLAIN ANALYZE INSERT INTO audit VALUES (1)")) is True
    assert classifier(text("PRAGMA foreign_keys = OFF")) is True


def test_workspace_mutex_reenters_only_in_the_same_logical_context(db):
    assessment_id = 918_271
    worker_started = threading.Event()
    worker_acquired = threading.Event()

    def competing_worker() -> None:
        worker_db = sessionmaker(bind=db.get_bind())()
        try:
            worker_started.set()
            with assessment_workspace_mutex(
                worker_db,
                assessment_id=assessment_id,
            ):
                worker_acquired.set()
        finally:
            worker_db.close()

    with assessment_workspace_mutex(db, assessment_id=assessment_id):
        # An eager in-process recovery task runs in the current logical context
        # and may enter without deadlocking on the lock it already owns.
        with assessment_workspace_mutex(db, assessment_id=assessment_id):
            pass

        worker = threading.Thread(target=competing_worker)
        worker.start()
        assert worker_started.wait(timeout=1)
        assert not worker_acquired.wait(timeout=0.05)

    assert worker_acquired.wait(timeout=1)
    worker.join(timeout=1)
    assert not worker.is_alive()


@pytest.mark.asyncio
async def test_async_workspace_mutex_waits_without_blocking_the_event_loop(db):
    assessment_id = 918_272
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_acquired = asyncio.Event()

    async def first_owner() -> None:
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=assessment_id,
        ):
            # Same-task nesting is sequential and must remain re-entrant.
            async with async_assessment_workspace_mutex(
                db,
                assessment_id=assessment_id,
            ):
                first_entered.set()
            await release_first.wait()

    async def competing_owner() -> None:
        await first_entered.wait()
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=assessment_id,
        ):
            second_acquired.set()

    first = asyncio.create_task(first_owner())
    second = asyncio.create_task(competing_owner())
    await first_entered.wait()
    await asyncio.sleep(0.05)
    assert not second_acquired.is_set()
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert second_acquired.is_set()


@pytest.mark.asyncio
async def test_cancelled_async_waiter_cannot_leak_the_workspace_lock(db):
    assessment_id = 918_273
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    cancelled_waiter_entered = False

    async def owner() -> None:
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=assessment_id,
        ):
            owner_entered.set()
            await release_owner.wait()

    async def cancelled_waiter() -> None:
        nonlocal cancelled_waiter_entered
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=assessment_id,
        ):
            cancelled_waiter_entered = True

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    waiter_task = asyncio.create_task(cancelled_waiter())
    await asyncio.sleep(0.05)
    waiter_task.cancel()
    release_owner.set()
    await owner_task
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter_task, timeout=1)
    assert cancelled_waiter_entered is False

    # The cancelled acquisition released its eventual lease, so a later
    # request can still enter instead of inheriting a permanently wedged lock.
    async with asyncio.timeout(1):
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=assessment_id,
        ):
            pass


def test_postgres_waiter_closes_each_missed_try_lock_connection(monkeypatch):
    engine = _FakePostgresEngine([False, False, True])
    db = _FakePostgresSession(engine)
    delays = []
    monkeypatch.setattr(
        workspace_serialization_module.time,
        "sleep",
        lambda delay: delays.append(delay),
    )
    monkeypatch.setattr(
        workspace_serialization_module,
        "_postgres_retry_sleep_seconds",
        lambda delay: delay,
    )

    with assessment_workspace_mutex(db, assessment_id=918_274):
        assert [connection.closed for connection in engine.connections] == [
            True,
            True,
            False,
        ]

    assert delays == [0.1, 0.2]
    assert all(connection.closed for connection in engine.connections)
    assert engine.connections[-1].unlock_calls == 1
    assert not any(connection.invalidated for connection in engine.connections)


def test_postgres_pool_checkout_pressure_retries_with_backoff(monkeypatch):
    engine = _FakePostgresEngine([True])
    db = _FakePostgresSession(engine)
    original_connect = engine.connect
    connect_calls = 0
    delays = []

    def connect():
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise workspace_serialization_module.SQLAlchemyTimeoutError("pool full")
        return original_connect()

    monkeypatch.setattr(engine, "connect", connect)
    monkeypatch.setattr(
        workspace_serialization_module.time,
        "sleep",
        lambda delay: delays.append(delay),
    )
    monkeypatch.setattr(
        workspace_serialization_module,
        "_postgres_retry_sleep_seconds",
        lambda delay: delay,
    )

    with assessment_workspace_mutex(db, assessment_id=918_279):
        pass

    assert connect_calls == 2
    assert delays == [0.1]
    assert engine.connections[0].unlock_calls == 1
    assert engine.connections[0].closed is True


def test_postgres_unlock_failure_discards_the_connection():
    engine = _FakePostgresEngine([True], unlock_result=False)
    db = _FakePostgresSession(engine)

    with pytest.raises(
        RuntimeError,
        match="PostgreSQL assessment workspace mutex was not held",
    ):
        with assessment_workspace_mutex(db, assessment_id=918_275):
            pass

    assert engine.connections[0].invalidated is True
    assert engine.connections[0].closed is True


def test_postgres_ambiguous_try_lock_failure_discards_the_connection():
    engine = _FakePostgresEngine([RuntimeError("connection lost")])
    db = _FakePostgresSession(engine)

    with pytest.raises(RuntimeError, match="connection lost"):
        with assessment_workspace_mutex(db, assessment_id=918_276):
            pass

    assert engine.connections[0].invalidated is True
    assert engine.connections[0].closed is True


@pytest.mark.asyncio
async def test_cancelled_postgres_waiter_stops_between_try_lock_attempts():
    engine = _FakePostgresEngine([False])
    db = _FakePostgresSession(engine)
    entered = False

    async def waiter() -> None:
        nonlocal entered
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=918_277,
        ):
            entered = True

    task = asyncio.create_task(waiter())
    async with asyncio.timeout(1):
        while not engine.connections or not engine.connections[0].closed:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert entered is False
    assert all(connection.closed for connection in engine.connections)
    assert not any(connection.invalidated for connection in engine.connections)


@pytest.mark.asyncio
async def test_cancelled_postgres_try_lock_releases_an_eventual_acquisition():
    try_started = threading.Event()
    release_try = threading.Event()
    engine = _FakePostgresEngine(
        [True],
        try_started=try_started,
        release_try=release_try,
    )
    db = _FakePostgresSession(engine)
    entered = False

    async def waiter() -> None:
        nonlocal entered
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=918_278,
        ):
            entered = True

    task = asyncio.create_task(waiter())
    assert await asyncio.to_thread(try_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    release_try.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert entered is False
    assert engine.connections[0].unlock_calls == 1
    assert engine.connections[0].closed is True
    assert engine.connections[0].invalidated is False
