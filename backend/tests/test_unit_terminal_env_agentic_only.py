"""Agentic-only posture: the candidate sandbox must NEVER receive the platform
Anthropic key (closes the `echo $ANTHROPIC_API_KEY` exfiltration path). The key
is for BACKEND use only, via resolve_backend_anthropic_key()."""
from __future__ import annotations

from app.components.assessments.terminal_runtime import (
    resolve_backend_anthropic_key,
    terminal_env,
)


def test_terminal_env_never_carries_the_platform_key():
    env = terminal_env(None)
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_API_KEY" not in env
    # Still suppresses interactive login in the sandbox.
    assert env.get("CLAUDE_CODE_SKIP_AUTH_LOGIN") == "1"


def test_backend_key_accessor_is_separate_and_returns_a_string(monkeypatch):
    import app.components.assessments.terminal_runtime as tr
    monkeypatch.setattr(tr.settings, "ANTHROPIC_API_KEY", "sk-test-123", raising=False)
    assert resolve_backend_anthropic_key() == "sk-test-123"
    # And that key is NOT leaked into the sandbox env.
    assert "sk-test-123" not in terminal_env(None).values()
