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
PRODUCTION_ENV_NAMES = frozenset({"prod", "production"})
RETIRED_CLAUDE_MODELS = frozenset(
    "claude-" + suffix
    for suffix in (
        "3-5-haiku-latest",
        "3-5-haiku-20241022",
        "3-haiku-20240307",
    )
)


def is_production_like(settings) -> bool:
    deployment_env = (
        getattr(settings, "DEPLOYMENT_ENV", "") or ""
    ).strip().lower()
    frontend_url = (getattr(settings, "FRONTEND_URL", "") or "").strip()
    return (
        deployment_env in PRODUCTION_ENV_NAMES
        or bool(getattr(settings, "SENTRY_DSN", None))
        or "localhost" not in frontend_url
    )


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
    production_like = is_production_like(settings)
    secret = (getattr(settings, "SECRET_KEY", "") or "").strip().lower()
    if production_like and secret in INSECURE_DEFAULTS:
        failures.append(
            "CRITICAL: SECRET_KEY is set to an insecure default. "
            "Set a strong SECRET_KEY in your .env before running in production."
        )

    usage_meter_live = bool(getattr(settings, "USAGE_METER_LIVE", False))
    usage_meter_emergency_override = bool(
        getattr(
            settings,
            "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY",
            False,
        )
    )
    if (
        production_like
        and not usage_meter_live
        and not usage_meter_emergency_override
    ):
        failures.append(
            "CRITICAL: USAGE_METER_LIVE must be true in production so credit "
            "debits and spend gates are enforced. Set USAGE_METER_LIVE=true. "
            "For a time-bounded metering incident only, set "
            "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY=true; this leaves "
            "the usage meter unready and /health degraded."
        )

    if production_like:
        if not (getattr(settings, "E2B_API_KEY", "") or "").strip():
            failures.append(
                "CRITICAL: E2B_API_KEY is required for the closed assessment workspace."
            )
        if not (getattr(settings, "E2B_TEMPLATE", "") or "").strip():
            failures.append(
                "CRITICAL: E2B_TEMPLATE must identify the verified offline assessment image."
            )
        if bool(getattr(settings, "LIVE_ASSESSMENT_DEMO_ENABLED", False)):
            failures.append(
                "CRITICAL: LIVE_ASSESSMENT_DEMO_ENABLED must remain false in production; "
                "the public showcase is fixture-backed."
            )
        if not bool(
            getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", True)
        ):
            failures.append(
                "CRITICAL: AUTO_GENERATE_ASSESSMENT_TASKS must be true in "
                "production so a published requisition can reach its "
                "battle-tested Turn on review without a manual Tasks workflow."
            )
        configured_models = {
            field: (getattr(settings, field, "") or "").strip()
            for field in (
                "CLAUDE_MODEL",
                "CLAUDE_SCORING_MODEL",
                "CLAUDE_SCORING_BATCH_MODEL",
                "CLAUDE_CHAT_MODEL",
                "CLAUDE_AGENT_AUTONOMOUS_MODEL",
            )
        }
        retired = [
            f"{field}={model}"
            for field, model in configured_models.items()
            if model.lower() in RETIRED_CLAUDE_MODELS
        ]
        if retired:
            failures.append(
                "CRITICAL: retired Anthropic model configured ("
                + ", ".join(retired)
                + "). Configure a currently supported, pinned Anthropic model ID."
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
