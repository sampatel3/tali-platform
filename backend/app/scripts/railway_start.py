from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from app.platform.config import settings
from app.platform.startup_validation import (
    collect_railway_failures,
    collect_railway_warnings,
    collect_startup_failures,
)


def _log(message: str) -> None:
    print(f"[railway-start] {message}", flush=True)


def _database_url() -> str:
    return os.environ.get("DATABASE_PUBLIC_URL") or settings.DATABASE_URL


def _database_target_label(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname:
        if parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
        return parsed.hostname
    if url.startswith("sqlite"):
        return "sqlite"
    return "database"


def _emit_configuration_messages() -> list[str]:
    failures = [
        *collect_startup_failures(settings),
        *collect_railway_failures(settings, os.environ),
    ]
    for warning in collect_railway_warnings(settings, os.environ):
        _log(f"WARNING: {warning}")
    for failure in failures:
        _log(f"ERROR: {failure}")
    return failures


def _wait_for_database(timeout_seconds: int, interval_seconds: float) -> None:
    database_url = _database_url()
    target = _database_target_label(database_url)
    deadline = time.monotonic() + timeout_seconds
    last_error = "unknown error"

    engine_kwargs: dict = {}
    if "sqlite" not in database_url:
        engine_kwargs["pool_pre_ping"] = True

    _log(f"Waiting for database connectivity ({target})...")
    while time.monotonic() < deadline:
        engine = None
        try:
            engine = create_engine(database_url, **engine_kwargs)
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
        f"[railway-start] ERROR: Timed out waiting for database connectivity ({target}) after "
        f"{timeout_seconds}s. Last error: {last_error}"
    )


def _run_checked(command: list[str], label: str) -> None:
    _log(f"Running {label}...")
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"[railway-start] ERROR: {label.capitalize()} failed with exit code {exc.returncode}."
        ) from exc


def _exec_uvicorn(port: str) -> None:
    # Default to 2 workers so a single slow request (e.g. an inline Claude
    # call from a recruiter-triggered endpoint) can't freeze the entire
    # web service. Override with TALI_WEB_WORKERS for tuning.
    workers = os.environ.get("TALI_WEB_WORKERS", "2").strip() or "2"
    _log(f"Starting uvicorn on port {port} (workers={workers})...")
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--workers",
            workers,
        ],
    )


def main(argv: list[str] | None = None) -> int:
    # Dispatch to the worker bootstrap when this same image is used as the
    # Celery worker service. Railway lets us share one image across services
    # so we don't have to maintain two railway.json files; the worker service
    # sets TALI_SERVICE_MODE=worker.
    if os.environ.get("TALI_SERVICE_MODE", "web").lower() == "worker":
        from .railway_worker_start import main as worker_main
        return worker_main()

    parser = argparse.ArgumentParser(description="Bootstrap Railway web startup safely.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate Railway config and exit without waiting for the database or starting the app.",
    )
    args = parser.parse_args(argv)

    failures = _emit_configuration_messages()
    if failures:
        return 1

    if args.check_only:
        _log("Configuration check passed.")
        return 0

    timeout_seconds = int(os.environ.get("RAILWAY_DB_WAIT_TIMEOUT_SECONDS", "90"))
    interval_seconds = float(os.environ.get("RAILWAY_DB_WAIT_INTERVAL_SECONDS", "2"))
    _wait_for_database(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    _run_checked([sys.executable, "-m", "alembic", "upgrade", "head"], "database migrations")
    _invalidate_stale_cv_match_scores()
    _exec_uvicorn(os.environ.get("PORT", "8000"))
    return 0


def _invalidate_stale_cv_match_scores() -> None:
    """Null out cv_match_score on rows whose cached score was produced
    under an older PROMPT_VERSION. Idempotent: re-running with no drift
    is a fast no-op. Wrapped so failures don't block startup."""
    if os.environ.get("DISABLE_STALE_SCORE_INVALIDATION", "").lower() == "true":
        _log("Stale CV score invalidation disabled via DISABLE_STALE_SCORE_INVALIDATION")
        return
    try:
        from app.cv_matching import PROMPT_VERSION
        from app.platform.database import SessionLocal
        from app.scripts.invalidate_stale_cv_scores import (
            invalidate_stale_cv_match_scores,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _log(f"WARNING: stale-cv-score invalidation skipped (import failed): {exc}")
        return
    db = SessionLocal()
    try:
        affected = invalidate_stale_cv_match_scores(db, PROMPT_VERSION)
        _log(
            f"Stale CV match scores nulled: {affected} (current={PROMPT_VERSION})"
        )
    except Exception as exc:  # pragma: no cover - defensive
        _log(f"WARNING: stale-cv-score invalidation failed: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
