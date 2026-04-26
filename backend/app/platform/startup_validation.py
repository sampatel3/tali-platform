from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse


INSECURE_DEFAULTS = frozenset({
    "",
    "changeme",
    "dev-secret-key-change-in-production",
    "secret",
})
LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_production_like(settings) -> bool:
    frontend_url = (getattr(settings, "FRONTEND_URL", "") or "").strip()
    return bool(getattr(settings, "SENTRY_DSN", None)) or "localhost" not in frontend_url


def url_points_to_localhost(url: str | None) -> bool:
    value = (url or "").strip()
    if not value:
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return host in LOCALHOST_HOSTS


def url_uses_sqlite(url: str | None) -> bool:
    value = (url or "").strip().lower()
    return value.startswith("sqlite:")


def is_railway_environment(environ: Mapping[str, str] | None = None) -> bool:
    env = environ or {}
    return bool(env.get("PORT")) or any(key.startswith("RAILWAY_") for key in env)


def collect_startup_failures(settings) -> list[str]:
    failures: list[str] = []
    secret = (getattr(settings, "SECRET_KEY", "") or "").strip().lower()
    if is_production_like(settings) and secret in INSECURE_DEFAULTS:
        failures.append(
            "CRITICAL: SECRET_KEY is set to an insecure default. "
            "Set a strong SECRET_KEY in your .env before running in production."
        )

    if not getattr(settings, "ASSESSMENT_TERMINAL_ENABLED", False):
        failures.append(
            "CRITICAL: ASSESSMENT_TERMINAL_ENABLED must be true. "
            "Assessments are terminal-only (Claude CLI) in production mode."
        )

    mode = (getattr(settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "") or "").strip().lower()
    if mode != "claude_cli_terminal":
        failures.append(
            "CRITICAL: ASSESSMENT_TERMINAL_DEFAULT_MODE must be claude_cli_terminal."
        )

    return failures


def collect_railway_failures(settings, environ: Mapping[str, str] | None = None) -> list[str]:
    if not is_railway_environment(environ):
        return []

    failures: list[str] = []
    database_url = (getattr(settings, "DATABASE_URL", "") or "").strip()
    if not database_url:
        failures.append(
            "DATABASE_URL is empty. Attach Railway PostgreSQL or set DATABASE_URL before booting."
        )
    elif url_uses_sqlite(database_url):
        failures.append(
            "DATABASE_URL is using sqlite, but Railway deployments require PostgreSQL."
        )
    elif url_points_to_localhost(database_url):
        failures.append(
            "DATABASE_URL points to localhost. Attach Railway PostgreSQL or set a shared DATABASE_URL before booting."
        )

    return failures


def collect_railway_warnings(settings, environ: Mapping[str, str] | None = None) -> list[str]:
    if not is_railway_environment(environ):
        return []

    warnings: list[str] = []
    redis_url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if not redis_url:
        warnings.append(
            "REDIS_URL is empty. The web app may boot, but Celery and /health Redis checks will fail."
        )
    elif url_points_to_localhost(redis_url):
        warnings.append(
            "REDIS_URL points to localhost. The web app may boot, but Celery and /health Redis checks will fail until Railway Redis is attached."
        )

    frontend_url = (getattr(settings, "FRONTEND_URL", "") or "").strip()
    if url_points_to_localhost(frontend_url):
        warnings.append(
            "FRONTEND_URL still points to localhost. Browser auth/CORS flows will fail until it is updated."
        )

    backend_url = (getattr(settings, "BACKEND_URL", "") or "").strip()
    if url_points_to_localhost(backend_url):
        warnings.append(
            "BACKEND_URL still points to localhost. Email links and webhook redirects will be incorrect until it is updated."
        )

    return warnings
