"""Terminal session helpers for Claude Code CLI runtime."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...models.assessment import Assessment
from ...models.organization import Organization
from ...models.task import Task
from ...platform.config import settings
from ...platform.secrets import decrypt_text
from ...services.assessment_repository_service import AssessmentRepositoryService
from .repository import append_assessment_timeline_event, utcnow


MAX_TRANSCRIPT_EVENTS = 2000


@dataclass
class TerminalSession:
    sandbox: Any
    handle: Any | None
    pid: int
    is_new: bool
    cli_available: bool
    error_message: str | None = None


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
    # Never allow interactive login prompts in candidate sessions.
    envs["CLAUDE_CODE_SKIP_AUTH_LOGIN"] = "1"
    if key:
        # Keep both env names for CLI compatibility across Claude Code versions.
        envs["ANTHROPIC_API_KEY"] = key
        envs["CLAUDE_API_KEY"] = key
    model = (settings.resolved_claude_model or "").strip()
    if model:
        envs["ANTHROPIC_MODEL"] = model
    return envs


def _build_claude_cli_command(*, repo_root: str) -> str:
    repo_guard_prompt = (
        "You are operating in a TAALI assessment sandbox. "
        f"Only read, create, and modify files under {repo_root}. "
        "Do not access paths outside this repository."
    )
    parts = [
        settings.CLAUDE_CLI_COMMAND,
        "--permission-mode",
        settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT,
        "--add-dir",
        repo_root,
        "--append-system-prompt",
        repo_guard_prompt,
    ]
    disallowed_tools = (settings.CLAUDE_CLI_DISALLOWED_TOOLS or "").strip()
    if disallowed_tools:
        parts.extend(["--disallowedTools", disallowed_tools])
    return " ".join(shlex.quote(str(part)) for part in parts if str(part).strip())


def _build_terminal_bootstrap_script(*, repo_root: str, cli_cmd: str) -> str:
    home = shlex.quote(repo_root)
    return (
        f"export HOME={home}\n"
        f"cd {home}\n"
        "taali_claude() {\n"
        "  if [ \"$#\" -eq 0 ]; then\n"
        f"    command {cli_cmd}\n"
        "    return $?\n"
        "  fi\n"
        "  case \"$1\" in\n"
        "    -*)\n"
        f"      command {cli_cmd} \"$@\"\n"
        "      return $?\n"
        "      ;;\n"
        "  esac\n"
        "  if [ \"$#\" -gt 1 ]; then\n"
        f"    command {cli_cmd} -p \"$*\"\n"
        "  else\n"
        f"    command {cli_cmd} -p \"$1\"\n"
        "  fi\n"
        "}\n"
        "taali_ask() {\n"
        "  if [ \"$#\" -gt 0 ]; then\n"
        "    taali_claude \"$*\"\n"
        "    return $?\n"
        "  fi\n"
        "  echo 'Paste your prompt. End with a new line containing only /send.'\n"
        "  local _taali_line\n"
        "  local _taali_prompt=''\n"
        "  while IFS= read -r _taali_line; do\n"
        "    if [ \"$_taali_line\" = \"/send\" ]; then\n"
        "      break\n"
        "    fi\n"
        "    if [ -n \"$_taali_prompt\" ]; then\n"
        "      _taali_prompt=\"${_taali_prompt}\"$'\\n'\"$_taali_line\"\n"
        "    else\n"
        "      _taali_prompt=\"$_taali_line\"\n"
        "    fi\n"
        "  done\n"
        "  if [ -z \"$(printf \"%s\" \"$_taali_prompt\" | tr -d \"[:space:]\")\" ]; then\n"
        "    echo 'No prompt provided. Usage: claude \"question\" or ask then paste text and /send.' >&2\n"
        "    return 2\n"
        "  fi\n"
        "  taali_claude \"$_taali_prompt\"\n"
        "}\n"
        "alias claude='taali_claude'\n"
        "alias ask='taali_ask'\n"
        "echo 'Claude Code CLI ready.'\n"
        "echo 'Quick tips: use Ask Claude (Cursor-style) in UI | claude \"question\" | ask (paste multi-line, end with /send)'\n"
    )


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


def _has_legacy_auto_exec_bootstrap(assessment: Assessment) -> bool:
    transcript = list(getattr(assessment, "cli_transcript", None) or [])
    for entry in reversed(transcript[-500:]):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_output":
            continue
        data = str(entry.get("data") or "")
        if "exec claude --permission-mode" in data:
            return True
    return False


def _has_legacy_prompt_wrapper(assessment: Assessment) -> bool:
    transcript = list(getattr(assessment, "cli_transcript", None) or [])
    for entry in reversed(transcript[-500:]):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_output":
            continue
        data = str(entry.get("data") or "")
        if 'Claude Code CLI ready. Type: claude "<your prompt>"' in data:
            return True
    return False


def _clone_assessment_branch_into_workspace(sandbox: Any, assessment: Assessment, task: Task) -> bool:
    repo_url = getattr(assessment, "assessment_repo_url", None)
    branch_name = getattr(assessment, "assessment_branch", None)
    if not repo_url or not branch_name:
        return False

    repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
    raw_repo_url = str(repo_url)
    if raw_repo_url.startswith("mock://"):
        mock_rel = raw_repo_url.replace("mock://", "", 1).strip("/")
        clone_url = str((repo_service.mock_root / mock_rel).resolve())
    else:
        clone_url = repo_service.authenticated_repo_url(raw_repo_url)

    repo_root = workspace_repo_root(task)
    result = sandbox.run_code(
        "import json, pathlib, subprocess\n"
        f"repo_root=pathlib.Path({repo_root!r})\n"
        "repo_root.parent.mkdir(parents=True, exist_ok=True)\n"
        "subprocess.run(['rm','-rf',str(repo_root)], check=False, capture_output=True)\n"
        f"args=['git','clone','--branch',{branch_name!r},{clone_url!r},str(repo_root)]\n"
        "p=subprocess.run(args, capture_output=True, text=True)\n"
        "payload={'returncode': p.returncode, 'stderr': p.stderr[-500:]}\n"
        "print(json.dumps(payload))\n"
    )
    try:
        stdout_text = ""
        if isinstance(result, dict):
            stdout_text = str(result.get("stdout") or "")
        else:
            logs = getattr(result, "logs", None)
            raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
            if isinstance(raw_stdout, list):
                stdout_text = "\n".join(str(item) for item in raw_stdout)
            elif raw_stdout is not None:
                stdout_text = str(raw_stdout)
            else:
                stdout_text = str(getattr(result, "stdout", "") or "")
        lines = stdout_text.strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
        return int(payload.get("returncode", 1)) == 0
    except Exception:
        return False


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

    try:
        sandbox = e2b_service.connect_sandbox(assessment.e2b_session_id)
        try:
            e2b_service.touch_sandbox(sandbox)
        except Exception:
            pass
    except Exception:
        # Recover from expired/deleted sandbox by creating a fresh one and re-cloning assessment repo.
        sandbox = e2b_service.create_sandbox()
        assessment.e2b_session_id = e2b_service.get_sandbox_id(sandbox)
        _mark_terminal_session(assessment, pid=None, state="stopped")
        append_assessment_timeline_event(
            assessment,
            "terminal_sandbox_recovered",
            {"sandbox_id": assessment.e2b_session_id},
        )
        append_cli_transcript(
            assessment,
            "terminal_sandbox_recovered",
            {"sandbox_id": assessment.e2b_session_id},
        )
        if not _clone_assessment_branch_into_workspace(sandbox, assessment, task):
            assessment.cli_session_state = "error"
            db.commit()
            raise RuntimeError(
                "Assessment sandbox expired and could not be restored automatically. "
                "Please restart the assessment session."
            )
        db.commit()
    pid = int(assessment.cli_session_pid or 0)
    if pid > 0:
        if _has_legacy_auto_exec_bootstrap(assessment) or _has_legacy_prompt_wrapper(assessment):
            try:
                e2b_service.kill_process(sandbox, pid)
            except Exception:
                pass
            append_assessment_timeline_event(
                assessment,
                "terminal_exit",
                {"pid": pid, "reason": "legacy_session_refresh"},
            )
            append_cli_transcript(
                assessment,
                "terminal_exit",
                {"pid": pid, "reason": "legacy_session_refresh"},
            )
            _mark_terminal_session(assessment, pid=None, state="stopped")
            db.commit()
        else:
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
    if not (envs.get("ANTHROPIC_API_KEY") or "").strip():
        _mark_terminal_session(assessment, pid=None, state="error")
        append_assessment_timeline_event(
            assessment,
            "terminal_error",
            {"reason": "missing_claude_api_key"},
        )
        append_cli_transcript(
            assessment,
            "terminal_error",
            {"reason": "missing_claude_api_key"},
        )
        db.commit()
        return TerminalSession(
            sandbox=sandbox,
            handle=None,
            pid=0,
            is_new=True,
            cli_available=False,
            error_message=(
                "Claude CLI authentication is not configured for this workspace. "
                "Set an organization Claude API key before starting terminal mode."
            ),
        )

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
            f"Install `{settings.CLAUDE_CLI_COMMAND}` in the sandbox template."
        )

    if cli_available:
        cli_cmd = _build_claude_cli_command(repo_root=repo_root)
        bootstrap_script = _build_terminal_bootstrap_script(repo_root=repo_root, cli_cmd=cli_cmd)
        # Keep candidates at an interactive shell prompt and provide a guarded Claude wrapper.
        # Auto-exec into Claude can leave sessions looking frozen if the CLI is waiting silently.
        e2b_service.send_pty_input(sandbox, pid, bootstrap_script)
        append_cli_transcript(
            assessment,
            "terminal_bootstrap",
            {"pid": pid, "version": 2},
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
