from app.platform.config import Settings


def test_resolved_claude_model_uses_non_prod_default():
    settings = Settings(
        DEPLOYMENT_ENV="staging",
        CLAUDE_MODEL=None,
        CLAUDE_MODEL_NON_PROD="claude-3-5-haiku-latest",
        CLAUDE_MODEL_PRODUCTION="claude-3-5-sonnet-20241022",
    )
    assert settings.resolved_claude_model == "claude-3-5-haiku-latest"


def test_resolved_claude_model_uses_production_default():
    settings = Settings(
        DEPLOYMENT_ENV="production",
        CLAUDE_MODEL=None,
        CLAUDE_MODEL_NON_PROD="claude-3-5-haiku-latest",
        CLAUDE_MODEL_PRODUCTION="claude-3-5-sonnet-20241022",
    )
    assert settings.resolved_claude_model == "claude-3-5-sonnet-20241022"


def test_resolved_claude_model_explicit_override_wins():
    settings = Settings(
        DEPLOYMENT_ENV="production",
        CLAUDE_MODEL="claude-custom-override",
        CLAUDE_MODEL_NON_PROD="claude-3-5-haiku-latest",
        CLAUDE_MODEL_PRODUCTION="claude-3-5-sonnet-20241022",
    )
    assert settings.resolved_claude_model == "claude-custom-override"
