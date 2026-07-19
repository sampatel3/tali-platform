from types import SimpleNamespace

import pytest

from app.platform.startup_validation import (
    collect_railway_failures,
    collect_railway_warnings,
    collect_startup_failures,
    is_production_like,
    url_points_to_localhost,
    url_uses_sqlite,
)

_STRONG_SECRET = "jwt-signing-secret-that-is-at-least-32-chars"
_STRONG_INTEGRATION_SECRET = "integration-secret-that-is-at-least-32-chars"
_STRONG_ADMIN_SECRET = "admin-health-secret-that-is-at-least-32-chars"


def _settings(**overrides):
    defaults = {
        "DEPLOYMENT_ENV": "development",
        "SENTRY_DSN": None,
        "FRONTEND_URL": "http://localhost:5173",
        "BACKEND_URL": "http://localhost:8000",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/app",
        "REDIS_URL": "redis://localhost:6379/0",
        "SECRET_KEY": "dev-secret-key-change-in-production",
        "INTEGRATION_ENCRYPTION_KEY": _STRONG_INTEGRATION_SECRET,
        "ADMIN_SECRET": _STRONG_ADMIN_SECRET,
        "TRUST_RAILWAY_X_REAL_IP": False,
        "BCRYPT_ROUNDS": 12,
        "RESEND_API_KEY": "re_live_configured",
        "ASSESSMENT_TERMINAL_ENABLED": True,
        "ASSESSMENT_TERMINAL_DEFAULT_MODE": "claude_cli_terminal",
        "USAGE_METER_LIVE": False,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY": False,
        "AUTO_GENERATE_ASSESSMENT_TASKS": True,
        "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_SCORING_MODEL": "",
        "CLAUDE_SCORING_BATCH_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_CHAT_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_AGENT_AUTONOMOUS_MODEL": "claude-sonnet-4-5-20250929",
        "CLAUDE_SEARCH_PARSER_MODEL": "",
        "CLAUDE_GROUNDING_MODEL": "",
        "GRAPHITI_LLM_MODEL": "claude-haiku-4-5-20251001",
        "GRAPHITI_LLM_SMALL_MODEL": "claude-haiku-4-5-20251001",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_is_production_like_when_frontend_is_non_localhost():
    assert is_production_like(_settings(FRONTEND_URL="https://app.taali.ai")) is True


def test_is_production_like_when_deployment_env_is_production():
    assert is_production_like(_settings(DEPLOYMENT_ENV="production")) is True


def test_collect_startup_failures_requires_strong_secret_in_production():
    failures = collect_startup_failures(
        _settings(FRONTEND_URL="https://app.taali.ai")
    )

    assert any("SECRET_KEY" in failure for failure in failures)


def test_collect_startup_failures_requires_distinct_integration_key_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            INTEGRATION_ENCRYPTION_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
        )
    )

    assert any("INTEGRATION_ENCRYPTION_KEY" in failure for failure in failures)


def test_collect_startup_failures_rejects_short_non_default_secrets():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="short-but-not-a-known-default",
            INTEGRATION_ENCRYPTION_KEY="another-short-custom-secret",
            ADMIN_SECRET="tiny-operator-secret",
            USAGE_METER_LIVE=True,
        )
    )

    assert any("SECRET_KEY" in failure for failure in failures)
    assert any("INTEGRATION_ENCRYPTION_KEY" in failure for failure in failures)
    assert any("ADMIN_SECRET" in failure for failure in failures)


def test_collect_startup_failures_requires_admin_secret_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            ADMIN_SECRET="",
            USAGE_METER_LIVE=True,
        )
    )

    assert any("ADMIN_SECRET" in failure for failure in failures)


def test_collect_startup_failures_requires_transactional_email_in_production():
    for unusable_key in ("", "skip", "your-resend-api-key"):
        failures = collect_startup_failures(
            _settings(
                DEPLOYMENT_ENV="production",
                SECRET_KEY=_STRONG_SECRET,
                RESEND_API_KEY=unusable_key,
                USAGE_METER_LIVE=True,
            )
        )
        assert any("RESEND_API_KEY" in failure for failure in failures)


def test_collect_startup_failures_requires_production_bcrypt_cost():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            BCRYPT_ROUNDS=4,
        )
    )

    assert any("BCRYPT_ROUNDS" in failure for failure in failures)


def test_collect_startup_failures_requires_complete_enabled_stripe_configuration():
    for overrides in (
        {"STRIPE_API_KEY": "", "STRIPE_WEBHOOK_SECRET": "whsec_live"},
        {"STRIPE_API_KEY": "sk_live", "STRIPE_WEBHOOK_SECRET": ""},
    ):
        failures = collect_startup_failures(
            _settings(
                DEPLOYMENT_ENV="production",
                SECRET_KEY=_STRONG_SECRET,
                USAGE_METER_LIVE=True,
                MVP_DISABLE_STRIPE=False,
                **overrides,
            )
        )

        assert any("enabled Stripe top-ups" in failure for failure in failures)


def test_collect_startup_failures_allows_complete_enabled_stripe_configuration():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            MVP_DISABLE_STRIPE=False,
            STRIPE_API_KEY="sk_live",
            STRIPE_WEBHOOK_SECRET="whsec_live",
        )
    )

    assert failures == []


def test_collect_startup_failures_requires_terminal_runtime_flags():
    failures = collect_startup_failures(
        _settings(
            FRONTEND_URL="https://app.taali.ai",
            SECRET_KEY=_STRONG_SECRET,
            ASSESSMENT_TERMINAL_ENABLED=False,
            ASSESSMENT_TERMINAL_DEFAULT_MODE="legacy",
        )
    )

    assert any("ASSESSMENT_TERMINAL_ENABLED" in failure for failure in failures)
    assert any("ASSESSMENT_TERMINAL_DEFAULT_MODE" in failure for failure in failures)


def test_collect_startup_failures_requires_live_usage_meter_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=False,
        )
    )

    assert any("USAGE_METER_LIVE must be true" in failure for failure in failures)


def test_collect_startup_failures_allows_narrow_usage_meter_emergency_override():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=False,
            USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY=True,
        )
    )

    assert failures == []


def test_collect_startup_failures_allows_live_usage_meter_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
        )
    )

    assert failures == []


def test_collect_startup_failures_requires_agentic_task_authoring_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            AUTO_GENERATE_ASSESSMENT_TASKS=False,
        )
    )

    assert any("AUTO_GENERATE_ASSESSMENT_TASKS" in failure for failure in failures)


def test_collect_startup_failures_rejects_retired_model_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            CLAUDE_MODEL="claude-3-5-haiku-latest",
        )
    )

    assert any("retired Anthropic model" in failure for failure in failures)


@pytest.mark.parametrize(
    "field_name",
    [
        "CLAUDE_MODEL",
        "CLAUDE_SCORING_MODEL",
        "CLAUDE_SCORING_BATCH_MODEL",
        "CLAUDE_CHAT_MODEL",
        "CLAUDE_AGENT_AUTONOMOUS_MODEL",
        "CLAUDE_SEARCH_PARSER_MODEL",
        "CLAUDE_GROUNDING_MODEL",
        "GRAPHITI_LLM_MODEL",
        "GRAPHITI_LLM_SMALL_MODEL",
    ],
)
def test_collect_startup_failures_rejects_unpriceable_model_overrides(
    field_name,
):
    unknown = "claude-opus-99-untrusted-secret-marker"
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            **{field_name: unknown},
        )
    )

    failure = next(
        item for item in failures if "has no configured pricing" in item
    )
    assert field_name in failure
    assert unknown not in failure


def test_collect_startup_failures_rejects_unpriceable_voyage_model():
    unknown = "voyage-secret-future-model"
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=_STRONG_SECRET,
            USAGE_METER_LIVE=True,
            VOYAGE_API_KEY="pa-live-configured",
            GRAPHITI_EMBEDDING_MODEL=unknown,
        )
    )

    failure = next(item for item in failures if "exact Voyage pricing" in item)
    assert unknown not in failure


def test_collect_railway_failures_flags_localhost_database_urls():
    failures = collect_railway_failures(
        _settings(DATABASE_URL="postgresql://user:pass@localhost:5432/app"),
        {"PORT": "8080"},
    )

    assert failures == [
        "DATABASE_URL points to localhost. Attach Railway PostgreSQL or set a shared DATABASE_URL before booting."
    ]


def test_collect_railway_failures_flags_sqlite_on_railway():
    failures = collect_railway_failures(
        _settings(DATABASE_URL="sqlite:///./test.db"),
        {"RAILWAY_ENVIRONMENT": "production"},
    )

    assert failures == [
        "DATABASE_URL is using sqlite, but Railway deployments require PostgreSQL."
    ]


def test_collect_railway_failures_requires_canonical_client_ip_in_production():
    failures = collect_railway_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            DATABASE_URL="postgresql://user:pass@postgres.railway.internal/app",
            TRUST_RAILWAY_X_REAL_IP=False,
        ),
        {"RAILWAY_ENVIRONMENT": "production"},
    )

    assert any("TRUST_RAILWAY_X_REAL_IP" in failure for failure in failures)

    configured = collect_railway_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            DATABASE_URL="postgresql://user:pass@postgres.railway.internal/app",
            TRUST_RAILWAY_X_REAL_IP=True,
        ),
        {"RAILWAY_ENVIRONMENT": "production"},
    )
    assert not any("TRUST_RAILWAY_X_REAL_IP" in failure for failure in configured)


def test_collect_railway_warnings_flag_localhost_service_urls():
    warnings = collect_railway_warnings(_settings(), {"PORT": "8080"})

    assert any("REDIS_URL" in warning for warning in warnings)
    assert any("FRONTEND_URL" in warning for warning in warnings)
    assert any("BACKEND_URL" in warning for warning in warnings)


def test_url_helpers_handle_localhost_and_sqlite():
    assert url_points_to_localhost("postgresql://user:pass@127.0.0.1:5432/app") is True
    assert url_points_to_localhost("https://app.taali.ai") is False
    assert url_uses_sqlite("sqlite:///./test.db") is True
    assert url_uses_sqlite("postgresql://user:pass@db.railway.internal:5432/app") is False
