from app.platform.config import Settings
import pytest


def test_resolved_claude_model_defaults_to_haiku_when_empty():
    settings = Settings(CLAUDE_MODEL="")
    assert settings.resolved_claude_model == "claude-3-5-haiku-latest"


def test_resolved_claude_model_defaults_to_haiku_when_whitespace():
    settings = Settings(CLAUDE_MODEL="   ")
    assert settings.resolved_claude_model == "claude-3-5-haiku-latest"


def test_resolved_claude_model_uses_explicit_config():
    settings = Settings(CLAUDE_MODEL="claude-custom-override")
    assert settings.resolved_claude_model == "claude-custom-override"


def test_resolved_claude_scoring_model_uses_claude_model():
    settings = Settings(CLAUDE_MODEL="claude-3-5-haiku-latest", CLAUDE_SCORING_MODEL="")
    assert settings.resolved_claude_scoring_model == "claude-3-5-haiku-latest"


def test_legacy_scoring_model_mismatch_fails_fast():
    with pytest.raises(ValueError):
        Settings(
            CLAUDE_MODEL="claude-3-5-haiku-latest",
            CLAUDE_SCORING_MODEL="claude-3-5-sonnet-20241022",
        )


def test_resolved_claude_scoring_model_uses_batch_override():
    settings = Settings(
        CLAUDE_MODEL="claude-sonnet-4-5",
        CLAUDE_SCORING_BATCH_MODEL="claude-3-5-haiku-latest",
    )
    assert settings.resolved_claude_scoring_model == "claude-3-5-haiku-latest"


def test_resolved_claude_scoring_model_falls_back_to_claude_model_when_batch_empty():
    settings = Settings(
        CLAUDE_MODEL="claude-sonnet-4-5",
        CLAUDE_SCORING_BATCH_MODEL="",
    )
    assert settings.resolved_claude_scoring_model == "claude-sonnet-4-5"
