from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse

import redis
from sqlalchemy import create_engine, text

from app.platform.config import settings
from app.platform.startup_validation import (
    collect_railway_failures,
    collect_railway_warnings,
    collect_startup_failures,
    url_points_to_localhost,
)


def _log(message: str) -> None:
    print(f"[railway-worker] {message}", flush=True)


def _database_url() -> str:
    return os.environ.get("DATABASE_PUBLIC_URL") or settings.DATABASE_URL


def _service_label(url: str, fallback: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname:
        if parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
        return parsed.hostname
    return fallback


def _configuration_failures() -> list[str]:
    failures = [
        *collect_startup_failures(settings),
        *collect_railway_failures(settings, os.environ),
    ]

    redis_url = (settings.REDIS_URL or "").strip()
    if not redis_url:
        failures.append(
            "REDIS_URL is empty. Attach Railway Redis or set REDIS_URL before booting the worker."
        )
    elif url_points_to_localhost(redis_url):
        failures.append(
            "REDIS_URL points to localhost. Attach Railway Redis or set a shared REDIS_URL before booting the worker."
        )

    for warning in collect_railway_warnings(settings, os.environ):
        # Worker already turns missing Redis into a hard error, so suppress the softer web warning.
        if "REDIS_URL" in warning:
            continue
        _log(f"WARNING: {warning}")

    for failure in failures:
        _log(f"ERROR: {failure}")
    return failures


def _wait_for_database(timeout_seconds: int, interval_seconds: float) -> None:
    database_url = _database_url()
    target = _service_label(database_url, "database")
    deadline = time.monotonic() + timeout_seconds
    last_error = "unknown error"

    _log(f"Waiting for database connectivity ({target})...")
    while time.monotonic() < deadline:
        engine = None
        try:
            engine = create_engine(database_url, pool_pre_ping=True)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            _log("Database connection is ready.")
            return
        except Exception as exc:  # pragma: no cover - exercised via deployment/runtime
            last_error = str(exc).splitlines()[0]
        finally:
            if engine is not None:
                engine.dispose()
        time.sleep(interval_seconds)

    raise SystemExit(
        f"[railway-worker] ERROR: Timed out waiting for database connectivity ({target}) after "
        f"{timeout_seconds}s. Last error: {last_error}"
    )


def _wait_for_redis(timeout_seconds: int, interval_seconds: float) -> None:
    target = _service_label(settings.REDIS_URL, "redis")
    deadline = time.monotonic() + timeout_seconds
    last_error = "unknown error"

    _log(f"Waiting for Redis connectivity ({target})...")
    while time.monotonic() < deadline:
        try:
            client = redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            if client.ping():
                _log("Redis connection is ready.")
                return
        except Exception as exc:  # pragma: no cover - exercised via deployment/runtime
            last_error = str(exc).splitlines()[0]
        time.sleep(interval_seconds)

    raise SystemExit(
        f"[railway-worker] ERROR: Timed out waiting for Redis connectivity ({target}) after "
        f"{timeout_seconds}s. Last error: {last_error}"
    )


def main() -> int:
    failures = _configuration_failures()
    if failures:
        return 1

    timeout_seconds = int(os.environ.get("RAILWAY_DEPENDENCY_WAIT_TIMEOUT_SECONDS", "90"))
    interval_seconds = float(os.environ.get("RAILWAY_DEPENDENCY_WAIT_INTERVAL_SECONDS", "2"))
    _wait_for_database(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    _wait_for_redis(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    _log("Starting Celery worker...")
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks",
            "worker",
            "--beat",
            "--loglevel=info",
            "--concurrency=2",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
