"""Small shared harness for tests that require an isolated PostgreSQL database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def configured_test_postgres_url() -> str:
    """Return the explicitly test-only PostgreSQL admin URL, if configured."""

    return os.environ.get("TEST_POSTGRES_URL", "").strip()


@contextmanager
def isolated_postgres_database(*, prefix: str) -> Iterator[str]:
    """Create and reliably remove one disposable database on the test server."""

    admin_url = configured_test_postgres_url()
    if not admin_url:
        raise RuntimeError("TEST_POSTGRES_URL is required")

    safe_prefix = "".join(character for character in prefix if character.isalnum() or character == "_")
    if not safe_prefix:
        raise ValueError("isolated PostgreSQL database prefix cannot be empty")
    database = f"{safe_prefix}_{uuid4().hex}"
    admin_engine = create_engine(
        admin_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        parsed = make_url(admin_url)
        scoped_url = parsed.set(database=database).render_as_string(hide_password=False)
        try:
            yield scoped_url
        finally:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
    finally:
        admin_engine.dispose()


def run_database_migrator(
    database_url: str,
    *,
    lock_timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the same supported migration wrapper used by deployment."""

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    if lock_timeout_seconds is not None:
        env["DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS"] = str(lock_timeout_seconds)
    return subprocess.run(
        [sys.executable, "-m", "app.scripts.database_migrate"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


__all__ = [
    "configured_test_postgres_url",
    "isolated_postgres_database",
    "run_database_migrator",
]
