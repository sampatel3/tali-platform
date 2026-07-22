from types import SimpleNamespace

from app.platform.startup_validation import (
    collect_railway_failures,
    collect_railway_warnings,
    collect_startup_failures,
    is_production_like,
    url_points_to_localhost,
    url_uses_sqlite,
)

_STRONG_ADMIN_SECRET = "dedicated-admin-secret-at-least-32-characters"


def _settings(**overrides):
    defaults = {
        "DEPLOYMENT_ENV": "development",
        "SENTRY_DSN": None,
        "FRONTEND_URL": "http://localhost:5173",
        "BACKEND_URL": "http://localhost:8000",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/app",
        "REDIS_URL": "redis://localhost:6379/0",
        "SECRET_KEY": "dev-secret-key-change-in-production",
        "ADMIN_SECRET": _STRONG_ADMIN_SECRET,
        "ASSESSMENT_TERMINAL_ENABLED": True,
        "ASSESSMENT_TERMINAL_DEFAULT_MODE": "claude_cli_terminal",
        "USAGE_METER_LIVE": False,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY": False,
        "AUTO_GENERATE_ASSESSMENT_TASKS": True,
        "E2B_API_KEY": "e2b_test_key",
        "E2B_TEMPLATE": "taali-assessment-offline-v1",
        "LIVE_ASSESSMENT_DEMO_ENABLED": False,
        "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_SCORING_MODEL": "",
        "CLAUDE_SCORING_BATCH_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_CHAT_MODEL": "claude-haiku-4-5-20251001",
        "CLAUDE_AGENT_AUTONOMOUS_MODEL": "",
        "AI_ROUTER_MODEL_OVERRIDES_JSON": "",
    }
    defaults.update(overrides)
    defaults.setdefault(
        "resolved_claude_model",
        str(defaults["CLAUDE_MODEL"] or "").strip() or "claude-haiku-4-5-20251001",
    )
    defaults.setdefault(
        "resolved_agent_autonomous_model",
        str(defaults["CLAUDE_AGENT_AUTONOMOUS_MODEL"] or "").strip()
        or str(defaults["resolved_claude_model"]),
    )
    return SimpleNamespace(**defaults)


def test_is_production_like_when_frontend_is_non_localhost():
    assert is_production_like(_settings(FRONTEND_URL="https://app.taali.ai")) is True


def test_is_production_like_when_deployment_env_is_production():
    assert is_production_like(_settings(DEPLOYMENT_ENV="production")) is True


def test_collect_startup_failures_requires_strong_secret_in_production():
    failures = collect_startup_failures(_settings(FRONTEND_URL="https://app.taali.ai"))

    assert any("SECRET_KEY" in failure for failure in failures)


def test_collect_startup_failures_requires_admin_secret_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="jwt-secret",
            ADMIN_SECRET="",
            USAGE_METER_LIVE=True,
        )
    )

    assert any("ADMIN_SECRET" in failure for failure in failures)


def test_collect_startup_failures_rejects_short_admin_secret_in_production():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="jwt-secret",
            ADMIN_SECRET="too-short",
            USAGE_METER_LIVE=True,
        )
    )

    assert any("ADMIN_SECRET" in failure for failure in failures)


def test_collect_startup_failures_rejects_jwt_key_reuse_for_admin_secret():
    shared_secret = "shared-secret-that-is-at-least-32-characters"
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY=shared_secret,
            ADMIN_SECRET=shared_secret,
            USAGE_METER_LIVE=True,
        )
    )

    assert any("ADMIN_SECRET" in failure for failure in failures)


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


def test_collect_startup_failures_requires_verified_closed_workspace_image():
    failures = collect_startup_failures(
        _settings(
            DEPLOYMENT_ENV="production",
            SECRET_KEY="real-secret",
            USAGE_METER_LIVE=True,
            E2B_API_KEY="",
            E2B_TEMPLATE="",
            LIVE_ASSESSMENT_DEMO_ENABLED=True,
        )
    )

    assert any("E2B_API_KEY" in failure for failure in failures)
    assert any("E2B_TEMPLATE" in failure for failure in failures)
    assert any("LIVE_ASSESSMENT_DEMO_ENABLED" in failure for failure in failures)


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


def test_collect_startup_failures_rejects_unregistered_legacy_selector():
    failures = collect_startup_failures(
        _settings(CLAUDE_AGENT_AUTONOMOUS_MODEL="claude-unregistered")
    )

    assert any("legacy model selector" in failure for failure in failures)


def test_collect_startup_failures_validates_legacy_selector_for_each_task(
    monkeypatch,
):
    # Haiku is registered, but it does not satisfy the parser's quality floor.
    # Startup must validate task compatibility, not merely alias existence.
    monkeypatch.setenv("CLAUDE_SEARCH_PARSER_MODEL", "haiku")

    failures = collect_startup_failures(_settings())

    assert any(
        "legacy model selector for candidate_search.parse" in failure
        for failure in failures
    )


def test_collect_startup_failures_checks_masked_legacy_selector():
    failures = collect_startup_failures(
        _settings(
            CLAUDE_MODEL="claude-unregistered",
            AI_ROUTER_MODEL_OVERRIDES_JSON=(
                '{"general_chat.orchestration":"haiku",'
                '"role_chat.orchestration":"haiku"}'
            ),
        )
    )

    assert any("legacy model selector" in failure for failure in failures)


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
    assert (
        url_uses_sqlite("postgresql://user:pass@db.railway.internal:5432/app") is False
    )
