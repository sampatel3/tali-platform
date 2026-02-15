"""Terminal session helpers for Claude Code CLI runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...models.assessment import Assessment
from ...models.organization import Organization
from ...models.task import Task
from ...platform.config import settings
from ...platform.secrets import decrypt_text
from .repository import append_assessment_timeline_event, utcnow


MAX_TRANSCRIPT_EVENTS = 2000


@dataclass
class TerminalSession:
    sandbox: Any
    handle: Any
    pid: int
    is_new: bool
    cli_available: bool
    error_message: str | None = None


def terminal_mode_enabled() -> bool:
    return bool(settings.ASSESSMENT_TERMINAL_ENABLED)


def resolve_ai_mode() -> str:
    mode = (settings.ASSESSMENT_TERMINAL_DEFAULT_MODE or "legacy_chat").strip().lower()
    if not terminal_mode_enabled():
        return "legacy_chat"
    if mode not in {"legacy_chat", "claude_cli_terminal"}:
        return "legacy_chat"
    return mode


def terminal_capabilities() -> dict:
    ai_mode = resolve_ai_mode()
    return {
        "enabled": terminal_mode_enabled(),
        "ws_protocol": "v1",
        "fallback_chat": True,
        "permission_mode": settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT,
        "command": settings.CLAUDE_CLI_COMMAND,
        "active_mode": ai_mode,
    }


def workspace_repo_root(task: Task) -> str:
    root_name = (task.task_key or f"assessment-{task.id}").strip() or f"assessment-{task.id}"
    safe_root = re.sub(r"[^a-zA-Z0-9._-]+", "-", root_name).strip("-") or f"assessment-{task.id}"
    return f"/workspace/{safe_root}"


def _resolve_claude_api_key(org: Organization | None) -> str:
    # Prefer org-scoped key when present.
    encrypted = getattr(org, "claude_api_key_encrypted", None) if org else None
    if encrypted:
        decrypted = decrypt_text(encrypted, settings.SECRET_KEY)
        if decrypted:
            return decrypted

    if settings.ASSESSMENT_TERMINAL_ALLOW_GLOBAL_KEY_FALLBACK:
        return settings.ANTHROPIC_API_KEY or ""
    return ""


def terminal_env(org: Organization | None) -> dict[str, str]:
    key = (_resolve_claude_api_key(org) or "").strip()
    envs: dict[str, str] = {}
    if key:
        envs["ANTHROPIC_API_KEY"] = key
    return envs


def append_cli_transcript(assessment: Assessment, event_type: str, payload: dict) -> None:
    transcript = list(getattr(assessment, "cli_transcript", None) or [])
    transcript.append(
        {
            "event_type": event_type,
            "timestamp": utcnow().isoformat(),
            **(payload or {}),
        }
    )
    if len(transcript) > MAX_TRANSCRIPT_EVENTS:
        transcript = transcript[-MAX_TRANSCRIPT_EVENTS:]
    assessment.cli_transcript = transcript


def _mark_terminal_session(
    assessment: Assessment,
    *,
    pid: int | None,
    state: str,
) -> None:
    now = utcnow()
    assessment.cli_session_pid = pid
    assessment.cli_session_state = state
    if state == "running":
        if not assessment.cli_session_started_at:
            assessment.cli_session_started_at = now
        assessment.cli_session_last_seen_at = now
    elif state in {"stopped", "exited", "error"}:
        assessment.cli_session_last_seen_at = now


def touch_terminal_session(assessment: Assessment) -> None:
    assessment.cli_session_last_seen_at = utcnow()


def stop_terminal_session(assessment: Assessment) -> None:
    _mark_terminal_session(assessment, pid=None, state="stopped")


def ensure_terminal_session(
    *,
    assessment: Assessment,
    task: Task,
    org: Organization | None,
    db: Session,
    e2b_service: Any,
) -> TerminalSession:
    if not assessment.e2b_session_id:
        raise RuntimeError("Assessment sandbox session is not initialized")

    sandbox = e2b_service.connect_sandbox(assessment.e2b_session_id)
    pid = int(assessment.cli_session_pid or 0)
    if pid > 0:
        try:
            handle = e2b_service.connect_process(sandbox, pid)
            _mark_terminal_session(assessment, pid=pid, state="running")
            touch_terminal_session(assessment)
            db.commit()
            return TerminalSession(
                sandbox=sandbox,
                handle=handle,
                pid=pid,
                is_new=False,
                cli_available=True,
            )
        except Exception:
            _mark_terminal_session(assessment, pid=None, state="stopped")
            db.commit()

    repo_root = workspace_repo_root(task)
    envs = terminal_env(org)
    handle = e2b_service.create_pty(
        sandbox,
        cwd=repo_root,
        envs=envs,
    )
    pid = int(handle.pid)
    _mark_terminal_session(assessment, pid=pid, state="running")
    append_assessment_timeline_event(
        assessment,
        "terminal_start",
        {
            "pid": pid,
            "cwd": repo_root,
        },
    )
    append_cli_transcript(
        assessment,
        "terminal_start",
        {"pid": pid, "cwd": repo_root},
    )

    cli_available = True
    error_message = None
    try:
        e2b_service.run_command(
            sandbox,
            f"{settings.CLAUDE_CLI_COMMAND} --version",
            cwd=repo_root,
            envs=envs,
            timeout=12,
        )
    except Exception:
        cli_available = False
        error_message = (
            f"Claude CLI is not available in this coding environment. "
            f"Install `{settings.CLAUDE_CLI_COMMAND}` in the sandbox template or use legacy chat."
        )

    if cli_available:
        e2b_service.send_pty_input(
            sandbox,
            pid,
            (
                f"{settings.CLAUDE_CLI_COMMAND} "
                f"--permission-mode {settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT}\n"
            ),
        )

    db.commit()
    return TerminalSession(
        sandbox=sandbox,
        handle=handle,
        pid=pid,
        is_new=True,
        cli_available=cli_available,
        error_message=error_message,
    )
