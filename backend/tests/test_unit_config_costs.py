from app.platform.config import Settings
import pytest


def test_resolved_claude_model_requires_explicit_value():
    settings = Settings(CLAUDE_MODEL="")
    with pytest.raises(RuntimeError, match="CLAUDE_MODEL is required"):
        _ = settings.resolved_claude_model


def test_resolved_claude_model_rejects_whitespace_value():
    settings = Settings(CLAUDE_MODEL="   ")
    with pytest.raises(RuntimeError, match="CLAUDE_MODEL is required"):
        _ = settings.resolved_claude_model


def test_resolved_claude_model_uses_explicit_config():
    settings = Settings(CLAUDE_MODEL="claude-custom-override")
    assert settings.resolved_claude_model == "claude-custom-override"
