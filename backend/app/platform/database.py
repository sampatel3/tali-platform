from collections.abc import Callable
from threading import Lock
from typing import AsyncGenerator
from weakref import WeakKeyDictionary

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings

# Runtime services must use the private/internal DATABASE_URL. Railway also
# exposes DATABASE_PUBLIC_URL for deploy-host migrations and local scripts; an
# earlier implicit preference here sent normal application traffic over that
# slower, billable public path whenever both variables were present.
def _runtime_database_url(config=settings) -> str:
    return str(config.DATABASE_URL)


_sync_database_url = _runtime_database_url()


def _postgres_engine_kwargs() -> dict:
    return {
        "pool_pre_ping": True,
        "pool_size": max(1, int(settings.DATABASE_POOL_SIZE)),
        "max_overflow": max(0, int(settings.DATABASE_MAX_OVERFLOW)),
        "pool_timeout": max(1, int(settings.DATABASE_POOL_TIMEOUT_SECONDS)),
        "pool_recycle": max(60, int(settings.DATABASE_POOL_RECYCLE_SECONDS)),
        # Reuse the hottest connection first so surplus connections remain idle
        # long enough for Postgres/proxies to retire them efficiently.
        "pool_use_lifo": True,
    }


def _workspace_lock_engine_kwargs() -> dict:
    """Strict pool budget for long-lived session advisory locks."""

    configured_size = int(settings.DATABASE_WORKSPACE_LOCK_POOL_SIZE)
    pool_size = (
        configured_size
        if configured_size > 0
        else max(
            1,
            int(settings.DATABASE_POOL_SIZE)
            + max(0, int(settings.DATABASE_MAX_OVERFLOW)),
        )
    )
    return {
        "pool_pre_ping": True,
        "pool_size": pool_size,
        "max_overflow": 0,
        "pool_timeout": max(1, int(settings.DATABASE_POOL_TIMEOUT_SECONDS)),
        "pool_recycle": max(60, int(settings.DATABASE_POOL_RECYCLE_SECONDS)),
        "pool_use_lifo": True,
    }


_WorkspaceLockEngineFactory = Callable[[], Engine]
_workspace_lock_factories_guard = Lock()
_workspace_lock_factories: WeakKeyDictionary[
    Engine, _WorkspaceLockEngineFactory
] = WeakKeyDictionary()


def register_workspace_lock_engine_factory(
    application_engine: Engine,
    factory: _WorkspaceLockEngineFactory,
) -> None:
    """Register an exact-config lock-engine factory for an injected DB engine.

    Production uses the canonical factory registered below. PostgreSQL contract
    tests or alternate application runtimes must register the same URL,
    credentials, query parameters, connect_args, and DBAPI creator they used for
    their application engine rather than asking lock code to reverse-engineer
    private QueuePool state.
    """

    if not callable(factory):
        raise TypeError("workspace lock engine factory must be callable")
    with _workspace_lock_factories_guard:
        _workspace_lock_factories[application_engine] = factory


def unregister_workspace_lock_engine_factory(application_engine: Engine) -> None:
    with _workspace_lock_factories_guard:
        _workspace_lock_factories.pop(application_engine, None)


def create_workspace_lock_engine(application_engine: Engine) -> Engine:
    """Create a fresh lock-only engine from an explicit exact-config factory."""

    with _workspace_lock_factories_guard:
        factory = _workspace_lock_factories.get(application_engine)
    if factory is None:
        raise RuntimeError(
            "No workspace lock engine factory is registered for this database engine"
        )
    lock_engine = factory()
    if lock_engine is application_engine:
        raise RuntimeError("workspace lock engine must not reuse the application engine")
    return lock_engine

# Sync engine (legacy, for non-auth routes until full async migration)
_sync_engine_kw: dict = {}
if "sqlite" not in _sync_database_url:
    _sync_engine_kw = _postgres_engine_kwargs()
engine = create_engine(_sync_database_url, **_sync_engine_kw)
if "sqlite" not in _sync_database_url:
    register_workspace_lock_engine_factory(
        engine,
        lambda: create_engine(
            _sync_database_url,
            **_workspace_lock_engine_kwargs(),
        ),
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency that yields a sync database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Async engine for FastAPI-Users (postgresql+asyncpg, or sqlite+aiosqlite for tests)
def _async_database_url() -> str:
    url = _sync_database_url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


_async_url = _async_database_url()
_async_engine_kw: dict = {}
if "sqlite" in _async_url:
    # Tests only (prod is Postgres). NullPool means every async request
    # opens a fresh connection to the shared in-memory DB instead of
    # reusing a pooled one. Pooled connections retain a stale snapshot
    # across the per-test create_all/drop_all cycle, which intermittently
    # broke FastAPI-Users' user lookup (→ spurious 401s) when certain
    # tests ran together. The sync test engine already uses NullPool for
    # the same reason. Timeout avoids "database is locked" with WAL.
    from sqlalchemy.pool import NullPool

    _async_engine_kw = {"connect_args": {"timeout": 30}, "poolclass": NullPool}
else:
    _async_engine_kw = _postgres_engine_kwargs()

async_engine = create_async_engine(_async_url, **_async_engine_kw)

async_session_maker = async_sessionmaker(
    async_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_maker() as session:
        yield session


class Base(DeclarativeBase):
    pass
