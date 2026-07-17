"""Cross-worker serialization for one candidate assessment workspace."""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event, Lock, get_ident
from typing import AsyncIterator, Iterator
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from ...platform.database import create_workspace_lock_engine

ASSESSMENT_WORKSPACE_LOCK_SCOPE = "assessment_workspace_runtime"
_POSTGRES_RETRY_INITIAL_SECONDS = 0.1
_POSTGRES_RETRY_MAX_SECONDS = 1.0
_SESSION_WRITE_FLAG = "assessment_workspace_mutex_uncommitted_write"
_READ_ONLY_TEXT_TOKENS = frozenset({"select", "show", "values"})


def _text_clause_may_write(statement: TextClause) -> bool:
    sql_text = str(statement).lstrip().split(None, 1)
    leading_token = sql_text[0].lower() if sql_text else ""
    # CTEs and unknown commands are intentionally conservative: a WITH body can
    # end in INSERT/UPDATE/DELETE, and rolling such a transaction back before a
    # workspace wait would silently discard the caller's write.
    return leading_token not in _READ_ONLY_TEXT_TOKENS


@event.listens_for(Session, "after_flush")
def _remember_workspace_session_flush(session: Session, _context) -> None:
    session.info[_SESSION_WRITE_FLAG] = True


@event.listens_for(Session, "do_orm_execute")
def _remember_workspace_session_dml(execute_state) -> None:
    if execute_state.is_select:
        return
    statement = execute_state.statement
    is_dml = bool(
        execute_state.is_insert
        or execute_state.is_update
        or execute_state.is_delete
    )
    if not is_dml and isinstance(statement, TextClause):
        is_dml = _text_clause_may_write(statement)
    if is_dml:
        execute_state.session.info[_SESSION_WRITE_FLAG] = True


@event.listens_for(Session, "after_transaction_end")
def _forget_workspace_session_write(session: Session, transaction) -> None:
    if transaction.parent is None:
        session.info.pop(_SESSION_WRITE_FLAG, None)


@dataclass
class _LocalLockEntry:
    lock: Lock
    users: int = 0


@dataclass
class _PostgresLockEngineEntry:
    pid: int
    engine: Engine


_LOCAL_LOCKS_GUARD = Lock()
_LOCAL_LOCKS: dict[int, _LocalLockEntry] = {}
_POSTGRES_LOCK_ENGINES_GUARD = Lock()
_POSTGRES_LOCK_ENGINES: WeakKeyDictionary[
    Engine, _PostgresLockEngineEntry
] = WeakKeyDictionary()
_WorkspaceOwnerKey = tuple[int, int, int | None]
_HELD_WORKSPACE_MUTEXES: ContextVar[frozenset[_WorkspaceOwnerKey]] = ContextVar(
    "held_assessment_workspace_mutexes",
    default=frozenset(),
)


async def _await_uninterruptibly(task: asyncio.Task):
    """Finish lock acquisition/release even if the caller is cancelled again."""

    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            continue


async def _run_blocking_cleanup(callback) -> None:
    cleanup = asyncio.create_task(asyncio.to_thread(callback))
    await _await_uninterruptibly(cleanup)


def _invalidate_and_close(connection) -> None:
    """Physically discard a connection whose session-lock state is uncertain."""

    try:
        connection.invalidate()
    finally:
        connection.close()


def _dispose_postgres_lock_engine(application_engine: Engine) -> None:
    """Dispose the lock-only pool when its owning application engine is reset."""

    with _POSTGRES_LOCK_ENGINES_GUARD:
        entry = _POSTGRES_LOCK_ENGINES.pop(application_engine, None)
    if entry is not None:
        # QueuePool.dispose() closes idle connections. Checked-out owners still
        # perform their explicit unlock before closing their lease.
        entry.engine.dispose(close=True)


def _application_engine_disposed(application_engine: Engine) -> None:
    _dispose_postgres_lock_engine(application_engine)


def _postgres_lock_engine(application_engine):
    """Return a bounded pool used only for session advisory-lock ownership.

    A workspace lock can span a long provider call. Holding that lease in the
    normal application QueuePool can exhaust every connection and prevent the
    same provider request from recording usage or finalizing its receipt. A
    registered platform factory preserves the original URL, credentials,
    connect_args, SSL configuration, and DBAPI creator while isolating leases.

    Non-Engine objects are lightweight test doubles and retain their injected
    connection behavior. Every real SQLAlchemy Engine is isolated.
    """

    if not isinstance(application_engine, Engine):
        return application_engine
    pid = os.getpid()
    with _POSTGRES_LOCK_ENGINES_GUARD:
        existing = _POSTGRES_LOCK_ENGINES.get(application_engine)
        if existing is not None and existing.pid == pid:
            return existing.engine
        if existing is not None:
            # A pre-fork pool must never donate inherited DBAPI sessions to the
            # child. ``close=False`` is SQLAlchemy's fork-safe pool replacement:
            # the parent keeps owning its live descriptors, while this process
            # creates a fresh lock-only engine below.
            existing.engine.dispose(close=False)
            _POSTGRES_LOCK_ENGINES.pop(application_engine, None)
        lock_engine = create_workspace_lock_engine(application_engine)
        _POSTGRES_LOCK_ENGINES[application_engine] = _PostgresLockEngineEntry(
            pid=pid,
            engine=lock_engine,
        )
        if not event.contains(
            application_engine,
            "engine_disposed",
            _application_engine_disposed,
        ):
            event.listen(
                application_engine,
                "engine_disposed",
                _application_engine_disposed,
            )
        return lock_engine


def _release_postgres_workspace_mutex(connection, parameters: dict[str, object]) -> None:
    """Release one session lock, discarding the connection on any uncertainty."""

    try:
        unlocked = connection.execute(
            text("SELECT pg_advisory_unlock(hashtext(:scope), :assessment_id)"),
            parameters,
        ).scalar_one()
        if unlocked is not True:
            raise RuntimeError("PostgreSQL assessment workspace mutex was not held")
    except BaseException:
        # A session-level advisory lock survives transaction rollback and a
        # normal pool check-in. If unlock failed (including an ambiguous network
        # failure), physically close the DBAPI connection so the server session
        # and every lock it may still own are destroyed before it can be reused.
        _invalidate_and_close(connection)
        raise
    connection.close()


def _try_acquire_postgres_workspace_mutex(
    engine,
    parameters: dict[str, object],
    *,
    cancelled: Event | None = None,
):
    """Try once without retaining a pooled connection while another owner waits."""

    connection = engine.connect()
    try:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
    except BaseException:
        connection.close()
        raise

    if cancelled is not None and cancelled.is_set():
        connection.close()
        return None

    try:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:scope), :assessment_id)"),
            parameters,
        ).scalar_one()
    except BaseException:
        # The server may have acquired the lock before the client observed the
        # failure. Never return that possibly locked session to the pool.
        _invalidate_and_close(connection)
        raise

    if acquired is not True:
        connection.close()
        return None

    if cancelled is not None and cancelled.is_set():
        _release_postgres_workspace_mutex(connection, parameters)
        return None
    return connection


def _next_postgres_retry_delay(delay: float) -> float:
    return min(_POSTGRES_RETRY_MAX_SECONDS, max(delay * 2.0, delay))


def _postgres_retry_sleep_seconds(delay: float) -> float:
    """Apply modest jitter so many waiters do not stampede the pool together."""

    return max(0.01, delay * random.uniform(0.8, 1.2))


def _workspace_owner_key(assessment_id: int) -> _WorkspaceOwnerKey:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (int(assessment_id), get_ident(), id(task) if task is not None else None)


def prepare_assessment_workspace_mutex(db: Session) -> None:
    """Release a read transaction before waiting without discarding writes."""

    if db.new or db.dirty or db.deleted or db.info.get(_SESSION_WRITE_FLAG):
        raise RuntimeError(
            "cannot wait for an assessment workspace mutex with uncommitted writes"
        )
    if db.in_transaction():
        db.rollback()
    if db.in_transaction():
        raise RuntimeError(
            "request transaction remained active before workspace mutex acquisition"
        )


@contextmanager
def _mark_workspace_mutex_held(assessment_id: int) -> Iterator[None]:
    owner_key = _workspace_owner_key(assessment_id)
    held = _HELD_WORKSPACE_MUTEXES.get()
    token = _HELD_WORKSPACE_MUTEXES.set(held | {owner_key})
    try:
        yield
    finally:
        _HELD_WORKSPACE_MUTEXES.reset(token)


@contextmanager
def _local_assessment_workspace_mutex(assessment_id: int) -> Iterator[None]:
    with _LOCAL_LOCKS_GUARD:
        entry = _LOCAL_LOCKS.setdefault(
            int(assessment_id),
            _LocalLockEntry(lock=Lock()),
        )
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _LOCAL_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _LOCAL_LOCKS.pop(int(assessment_id), None)


@contextmanager
def assessment_workspace_mutex(
    db: Session,
    *,
    assessment_id: int,
) -> Iterator[None]:
    """Serialize detached workspace work without retaining an ORM transaction.

    PostgreSQL uses a session advisory lock on a dedicated autocommit
    connection. Tests and local SQLite use a process-local keyed mutex. The
    caller remains free to commit/rollback its request session while ownership
    is retained across E2B and repository-provider calls.

    Re-entry from the same logical execution context is safe and must not
    acquire a second lock. This matters when Celery executes an after-commit
    recovery task eagerly in-process: the nested task is sequential work owned
    by the current lock holder, while another request/thread/context still has
    to wait for the outer lock to be released.
    """

    assessment_id = int(assessment_id)
    if _workspace_owner_key(assessment_id) in _HELD_WORKSPACE_MUTEXES.get():
        yield
        return

    if isinstance(db, Session) and db.in_transaction():
        raise RuntimeError(
            "prepare_assessment_workspace_mutex must run before mutex acquisition"
        )

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        with _local_assessment_workspace_mutex(assessment_id):
            with _mark_workspace_mutex_held(assessment_id):
                yield
        return

    application_engine = getattr(bind, "engine", bind)
    engine = _postgres_lock_engine(application_engine)
    parameters = {
        "scope": ASSESSMENT_WORKSPACE_LOCK_SCOPE,
        "assessment_id": assessment_id,
    }
    retry_delay = _POSTGRES_RETRY_INITIAL_SECONDS
    connection = None
    while connection is None:
        try:
            connection = _try_acquire_postgres_workspace_mutex(engine, parameters)
        except SQLAlchemyTimeoutError:
            # Another unrelated request can briefly occupy every pool slot. A
            # lock waiter has no work to roll back, so treat checkout pressure
            # like a missed try-lock and preserve the indefinite wait contract.
            connection = None
        if connection is not None:
            break
        time.sleep(_postgres_retry_sleep_seconds(retry_delay))
        retry_delay = _next_postgres_retry_delay(retry_delay)
    try:
        with _mark_workspace_mutex_held(assessment_id):
            yield
    finally:
        _release_postgres_workspace_mutex(connection, parameters)


@asynccontextmanager
async def async_assessment_workspace_mutex(
    db: Session,
    *,
    assessment_id: int,
) -> AsyncIterator[None]:
    """Async-safe form of :func:`assessment_workspace_mutex`.

    Lock acquisition and PostgreSQL advisory-lock I/O run off the event loop.
    A second async request therefore waits without preventing the current
    owner from finishing its provider call and releasing the same lock.
    """

    assessment_id = int(assessment_id)
    if _workspace_owner_key(assessment_id) in _HELD_WORKSPACE_MUTEXES.get():
        yield
        return

    if isinstance(db, Session) and db.in_transaction():
        raise RuntimeError(
            "prepare_assessment_workspace_mutex must run before mutex acquisition"
        )

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        local_lock = _local_assessment_workspace_mutex(assessment_id)
        acquisition = asyncio.create_task(asyncio.to_thread(local_lock.__enter__))
        try:
            await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            try:
                await _await_uninterruptibly(acquisition)
            except Exception:
                pass
            else:
                await _run_blocking_cleanup(
                    lambda: local_lock.__exit__(None, None, None)
                )
            raise
        try:
            with _mark_workspace_mutex_held(assessment_id):
                yield
        finally:
            await _run_blocking_cleanup(
                lambda: local_lock.__exit__(None, None, None)
            )
        return

    application_engine = getattr(bind, "engine", bind)
    engine = _postgres_lock_engine(application_engine)
    parameters = {
        "scope": ASSESSMENT_WORKSPACE_LOCK_SCOPE,
        "assessment_id": assessment_id,
    }

    cancelled = Event()
    retry_delay = _POSTGRES_RETRY_INITIAL_SECONDS
    connection = None
    while connection is None:
        acquisition = asyncio.create_task(
            asyncio.to_thread(
                _try_acquire_postgres_workspace_mutex,
                engine,
                parameters,
                cancelled=cancelled,
            )
        )
        try:
            connection = await asyncio.shield(acquisition)
        except SQLAlchemyTimeoutError:
            connection = None
        except asyncio.CancelledError:
            cancelled.set()
            try:
                acquired_after_cancel = await _await_uninterruptibly(acquisition)
            except Exception:
                # The bounded try-lock helper invalidates the connection on an
                # ambiguous statement failure, so there is no lease left here.
                pass
            else:
                if acquired_after_cancel is not None:
                    await _run_blocking_cleanup(
                        lambda: _release_postgres_workspace_mutex(
                            acquired_after_cancel,
                            parameters,
                        )
                    )
            raise
        if connection is not None:
            break
        await asyncio.sleep(_postgres_retry_sleep_seconds(retry_delay))
        retry_delay = _next_postgres_retry_delay(retry_delay)
    try:
        with _mark_workspace_mutex_held(assessment_id):
            yield
    finally:
        await _run_blocking_cleanup(
            lambda: _release_postgres_workspace_mutex(connection, parameters)
        )


__all__ = [
    "ASSESSMENT_WORKSPACE_LOCK_SCOPE",
    "assessment_workspace_mutex",
    "async_assessment_workspace_mutex",
    "prepare_assessment_workspace_mutex",
]
