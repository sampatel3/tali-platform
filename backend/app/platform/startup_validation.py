from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from ..services.claude_model_pricing import is_priceable_claude_model
from ..services.voyage_pricing import is_priceable_voyage_model


INSECURE_DEFAULTS = frozenset({
    "",
    "changeme",
    "dev-secret-key-change-in-production",
    "secret",
})
MIN_PRODUCTION_SECRET_LENGTH = 32
UNUSABLE_SERVICE_SECRETS = INSECURE_DEFAULTS | frozenset({
    "skip",
    "your-resend-api-key",
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
    secret_raw = (getattr(settings, "SECRET_KEY", "") or "").strip()
    secret = secret_raw.lower()
    if production_like and (
        secret in INSECURE_DEFAULTS
        or len(secret_raw) < MIN_PRODUCTION_SECRET_LENGTH
    ):
        failures.append(
            "CRITICAL: SECRET_KEY must be a non-default secret of at least "
            f"{MIN_PRODUCTION_SECRET_LENGTH} characters in production."
        )
    integration_key = (
        getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "") or ""
    ).strip()
    if production_like and (
        integration_key.lower() in INSECURE_DEFAULTS
        or len(integration_key) < MIN_PRODUCTION_SECRET_LENGTH
        or integration_key == secret_raw
    ):
        failures.append(
            "CRITICAL: INTEGRATION_ENCRYPTION_KEY must be a distinct, non-default "
            f"secret of at least {MIN_PRODUCTION_SECRET_LENGTH} characters so JWT "
            "rotation cannot invalidate provider credentials."
        )
    admin_secret = (getattr(settings, "ADMIN_SECRET", "") or "").strip()
    if production_like and (
        admin_secret.lower() in INSECURE_DEFAULTS
        or len(admin_secret) < MIN_PRODUCTION_SECRET_LENGTH
        or admin_secret in {secret_raw, integration_key}
    ):
        failures.append(
            "CRITICAL: ADMIN_SECRET must be a distinct, non-default "
            f"secret of at least {MIN_PRODUCTION_SECRET_LENGTH} characters."
        )

    # Password login now requires verified email. Without a working Resend key,
    # production can create an account that has no path to verify or sign in.
    resend_key = (getattr(settings, "RESEND_API_KEY", "") or "").strip()
    if production_like and resend_key.lower() in UNUSABLE_SERVICE_SECRETS:
        failures.append(
            "CRITICAL: RESEND_API_KEY must be configured in production because "
            "email verification is required before login."
        )

    if production_like and int(getattr(settings, "BCRYPT_ROUNDS", 12) or 0) < 12:
        failures.append(
            "CRITICAL: BCRYPT_ROUNDS must be at least 12 in production."
        )

    stripe_enabled = not bool(getattr(settings, "MVP_DISABLE_STRIPE", True))
    stripe_api_key = (getattr(settings, "STRIPE_API_KEY", "") or "").strip()
    stripe_webhook_secret = (
        getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""
    ).strip()
    if production_like and stripe_enabled and (
        not stripe_api_key or not stripe_webhook_secret
    ):
        failures.append(
            "CRITICAL: enabled Stripe top-ups require both STRIPE_API_KEY and "
            "STRIPE_WEBHOOK_SECRET so completed payments can grant credits."
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
                "CLAUDE_SEARCH_PARSER_MODEL",
                "CLAUDE_GROUNDING_MODEL",
                "GRAPHITI_LLM_MODEL",
                "GRAPHITI_LLM_SMALL_MODEL",
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
        unpriceable = [
            field
            for field, model in configured_models.items()
            if model
            and model.lower() not in RETIRED_CLAUDE_MODELS
            and not is_priceable_claude_model(model)
        ]
        if unpriceable:
            failures.append(
                "CRITICAL: Anthropic model has no configured pricing ("
                + ", ".join(unpriceable)
                + "). Add a verified rate before enabling it."
            )
        voyage_key = (getattr(settings, "VOYAGE_API_KEY", "") or "").strip()
        voyage_model = (
            getattr(settings, "GRAPHITI_EMBEDDING_MODEL", "") or ""
        ).strip()
        if voyage_key and not is_priceable_voyage_model(voyage_model):
            failures.append(
                "CRITICAL: GRAPHITI_EMBEDDING_MODEL has no exact Voyage pricing. "
                "Configure a reviewed text-embedding model before enabling Graphiti."
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

    if is_production_like(settings) and not bool(
        getattr(settings, "TRUST_RAILWAY_X_REAL_IP", False)
    ):
        failures.append(
            "TRUST_RAILWAY_X_REAL_IP must be true for a production Railway "
            "service so per-client rate limits use Railway's canonical "
            "X-Real-IP instead of the shared edge-proxy address."
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
