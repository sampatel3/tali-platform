import os
from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings

# Prefer public DB URL when set (so railway run from local can reach Postgres)
_sync_database_url = os.environ.get("DATABASE_PUBLIC_URL") or settings.DATABASE_URL

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
    # Timeout to avoid "database is locked" when sync+async share same file (e.g. tests)
    _async_engine_kw = {"connect_args": {"timeout": 30}}
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
