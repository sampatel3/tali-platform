from types import SimpleNamespace

from app.platform.startup_validation import (
    collect_railway_failures,
    collect_railway_warnings,
    collect_startup_failures,
    is_production_like,
    url_points_to_localhost,
    url_uses_sqlite,
)


def _settings(**overrides):
    defaults = {
        "DEPLOYMENT_ENV": "development",
        "SENTRY_DSN": None,
        "FRONTEND_URL": "http://localhost:5173",
        "BACKEND_URL": "http://localhost:8000",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/app",
        "REDIS_URL": "redis://localhost:6379/0",
        "SECRET_KEY": "dev-secret-key-change-in-production",
        "ASSESSMENT_TERMINAL_ENABLED": True,
        "ASSESSMENT_TERMINAL_DEFAULT_MODE": "claude_cli_terminal",
        "USAGE_METER_LIVE": False,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY": False,
        "AUTO_GENERATE_ASSESSMENT_TASKS": True,
        "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_SCORING_MODEL": "",
        "CLAUDE_SCORING_BATCH_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_CHAT_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_AGENT_AUTONOMOUS_MODEL": "claude-sonnet-4-20250514",
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


def test_collect_startup_failures_requires_terminal_runtime_flags():
    failures = collect_startup_failures(
        _settings(
            FRONTEND_URL="https://app.taali.ai",
            SECRET_KEY="real-secret",
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
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=False,
        )
    )

    assert any("USAGE_METER_LIVE must be true" in failure for failure in failures)


def test_collect_startup_failures_allows_narrow_usage_meter_emergency_override():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=False,
            USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY=True,
        )
    )

    assert failures == []


def test_collect_startup_failures_allows_live_usage_meter_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=True,
        )
    )

    assert failures == []


def test_collect_startup_failures_requires_agentic_task_authoring_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=True,
            AUTO_GENERATE_ASSESSMENT_TASKS=False,
        )
    )

    assert any("AUTO_GENERATE_ASSESSMENT_TASKS" in failure for failure in failures)


def test_collect_startup_failures_rejects_retired_model_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=True,
            CLAUDE_MODEL="claude-3-5-haiku-latest",
        )
    )

    assert any("retired Anthropic model" in failure for failure in failures)


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
