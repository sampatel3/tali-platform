from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings
from .database_url import runtime_database_url

# Deployed Railway replicas use the private network; local ``railway run``
# processes use the public proxy because ``*.railway.internal`` is unreachable.
_sync_database_url = runtime_database_url(settings.DATABASE_URL)

# Sync engine (legacy, for non-auth routes until full async migration)
_sync_engine_kw: dict = {}
if "sqlite" not in _sync_database_url:
    _sync_engine_kw = {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}
engine = create_engine(_sync_database_url, **_sync_engine_kw)

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
    _async_engine_kw = {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}

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
