"""Fixtures for the required migrated-PostgreSQL search gate.

The repository-wide fixture intentionally uses SQLite. Candidate-search SQL is
PostgreSQL-specific, so this suite binds an independent session to a CI-owned
database without changing the ordinary test engine or application settings.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


_EXPECTED_DATABASE = "taali_search_test"


@pytest.fixture(scope="session")
def postgres_search_engine():
    raw_url = (os.getenv("TALI_SEARCH_TEST_DATABASE_URL") or "").strip()
    if not raw_url:
        pytest.fail(
            "TALI_SEARCH_TEST_DATABASE_URL is required for the PostgreSQL "
            "candidate-search gate",
            pytrace=False,
        )
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or url.database != _EXPECTED_DATABASE:
        pytest.fail(
            "refusing candidate-search integration tests outside the explicit "
            f"PostgreSQL database {_EXPECTED_DATABASE!r}",
            pytrace=False,
        )

    engine = create_engine(raw_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                pytest.fail("candidate-search integration engine is not PostgreSQL")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def postgres_search_db(postgres_search_engine) -> Session:
    """Rollback every case while allowing the API and tools to share a session."""

    connection = postgres_search_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
