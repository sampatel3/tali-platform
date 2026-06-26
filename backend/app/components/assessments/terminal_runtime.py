"""Assessment runtime helpers — ai-mode, capabilities, repo root, sandbox env.

What remains after the legacy PTY-terminal removal: the small set of helpers the
agentic chat + assessment-start flow still need. ``resolve_ai_mode`` /
``terminal_capabilities`` are surfaced in the start payload; ``workspace_repo_root``
locates the sandbox repo; ``resolve_backend_anthropic_key`` returns the
server-side-only platform key; and ``terminal_env`` documents the agentic-only
posture — the candidate sandbox NEVER receives the platform Anthropic key. The
PTY terminal-session machinery was deleted with the terminal WS route.
"""

from __future__ import annotations

from ...models.organization import Organization
from ...models.task import Task
from ...platform.config import settings
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root


def terminal_mode_enabled() -> bool:
    return bool(settings.ASSESSMENT_TERMINAL_ENABLED)


def resolve_ai_mode() -> str:
    mode = (settings.ASSESSMENT_TERMINAL_DEFAULT_MODE or "").strip().lower()
    if not terminal_mode_enabled():
        raise RuntimeError("Assessment terminal mode is disabled by configuration")
    if mode != "claude_cli_terminal":
        raise RuntimeError("Invalid assessment AI mode; only claude_cli_terminal is supported")
    return "claude_cli_terminal"


def terminal_capabilities() -> dict:
    ai_mode = resolve_ai_mode()
    return {
        "enabled": terminal_mode_enabled(),
        "ws_protocol": "v1",
        "permission_mode": settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT,
        "command": settings.CLAUDE_CLI_COMMAND,
        "active_mode": ai_mode,
    }


def workspace_repo_root(task: Task) -> str:
    return canonical_workspace_repo_root(task)


def resolve_backend_anthropic_key() -> str:
    """The platform Anthropic key, for BACKEND use only (agentic chat / scoring
    call Anthropic server-side). This key is NEVER injected into a candidate
    sandbox — see ``terminal_env``."""
    return (settings.ANTHROPIC_API_KEY or "").strip()


def terminal_env(org: Organization | None) -> dict[str, str]:
    # Agentic-only posture (2026-06): the candidate sandbox NEVER receives the
    # platform Anthropic key — the AI runs server-side via the agentic chat. A
    # scoped, revocable per-session key could be reintroduced here later WITHOUT
    # changing callers.
    del org
    envs: dict[str, str] = {"CLAUDE_CODE_SKIP_AUTH_LOGIN": "1"}
    model = (settings.resolved_claude_model or "").strip()
    if model:
        envs["ANTHROPIC_MODEL"] = model
    return envs
