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
