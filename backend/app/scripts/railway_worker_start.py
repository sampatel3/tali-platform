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
from app.scripts.startup_error_evidence import startup_error_code


def _log(message: str) -> None:
    print(f"[railway-worker] {message}", flush=True)


def _database_url() -> str:
    # Workers run inside Railway and must probe the same private database URL
    # used by their SQLAlchemy sessions.  The public URL is only for migration
    # commands executed from an external deploy host.
    return str(settings.DATABASE_URL)


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
    last_error = "database_connectivity:unexpected:Error"

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
            last_error = startup_error_code(exc, operation="database_connectivity")
        finally:
            if engine is not None:
                engine.dispose()
        time.sleep(interval_seconds)

    raise SystemExit(
        f"[railway-worker] ERROR: Timed out waiting for database connectivity ({target}) after "
        f"{timeout_seconds}s. Last error code: {last_error}"
    )


def _wait_for_redis(timeout_seconds: int, interval_seconds: float) -> None:
    target = _service_label(settings.REDIS_URL, "redis")
    deadline = time.monotonic() + timeout_seconds
    last_error = "redis_connectivity:unexpected:Error"

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
            last_error = startup_error_code(exc, operation="redis_connectivity")
        time.sleep(interval_seconds)

    raise SystemExit(
        f"[railway-worker] ERROR: Timed out waiting for Redis connectivity ({target}) after "
        f"{timeout_seconds}s. Last error code: {last_error}"
    )


def main() -> int:
    failures = _configuration_failures()
    if failures:
        return 1

    timeout_seconds = int(os.environ.get("RAILWAY_DEPENDENCY_WAIT_TIMEOUT_SECONDS", "90"))
    interval_seconds = float(os.environ.get("RAILWAY_DEPENDENCY_WAIT_INTERVAL_SECONDS", "2"))
    _wait_for_database(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    _wait_for_redis(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    # Queues consumed by this worker. By default the single Railway
    # worker process consumes both `celery` (general tasks: emails,
    # Workable sync, etc.) and `scoring` (cv_match_v3.0 score +
    # batch_score). When we peel scoring off onto a second Railway
    # service later, set TALI_WORKER_QUEUES="scoring" on that service
    # and "celery" on this one — see backend/docs/CELERY_QUEUES.md.
    queues = os.environ.get("TALI_WORKER_QUEUES", "celery,scoring").strip() or "celery,scoring"
    # Bumped from 2 → 4 so Workable syncs (split across jobs-only,
    # starred, agent-mode, and nightly tasks per the 2026-05-20
    # redesign) can't claim every slot. With per-task Redis locks
    # scoring keeps at least 2-3 slots free in practice.
    concurrency = os.environ.get("TALI_WORKER_CONCURRENCY", "4")
    # `--beat` runs the periodic-task scheduler in-process. Only the
    # worker that owns scheduling should set this (TALI_WORKER_BEAT).
    # Default = beat enabled (single-worker setup).
    beat_enabled = os.environ.get("TALI_WORKER_BEAT", "true").strip().lower() != "false"

    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.tasks",
        "worker",
        f"--queues={queues}",
        f"--concurrency={concurrency}",
        "--loglevel=info",
    ]
    if beat_enabled:
        cmd.insert(cmd.index("worker") + 1, "--beat")

    _log(f"Starting Celery worker (queues={queues}, concurrency={concurrency}, beat={beat_enabled})...")
    os.execvp(sys.executable, cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
