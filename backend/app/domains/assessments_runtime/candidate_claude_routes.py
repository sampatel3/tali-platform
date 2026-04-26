from __future__ import annotations

import re
import time
from typing import Any
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.terminal_runtime import terminal_capabilities, terminal_env
from ...components.integrations.claude.service import ClaudeService
from ...models.organization import Organization
from ...models.task import Task
from ...platform.database import get_db
from ...schemas.assessment import ClaudeRequest
from ...services.task_repo_service import normalize_repo_files

router = APIRouter()

_MAX_HISTORY_MESSAGES = 20
_MAX_CONTEXT_CHARS = 12000
_MAX_REPO_FILES_IN_PROMPT = 120
_MAX_REPO_FILE_EXCERPTS = 8
_MAX_REPO_FILE_CHARS = 1800
_INTERNAL_TOOL_TAGS = {
    "read_file",
    "read_many_files",
    "list_dir",
    "glob_search",
    "grep_search",
    "search_files",
    "run_command",
    "open_file",
}
_TOOL_BLOCK_RE = re.compile(r"<([a-z_][a-z0-9_]*)>\s*([\s\S]*?)</\1>", re.IGNORECASE)
_PATH_TAG_RE = re.compile(r"<path>\s*([\s\S]*?)\s*</path>", re.IGNORECASE)


def _normalize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:4000]})
    if len(normalized) > _MAX_HISTORY_MESSAGES:
        return normalized[-_MAX_HISTORY_MESSAGES:]
    return normalized


def _repo_outline(task: Task) -> str:
    paths = sorted(_request_repo_files(task, None).keys())
    if not paths:
        return "No repository files available."
    paths = paths[:_MAX_REPO_FILES_IN_PROMPT]
    return "\n".join(f"- {path}" for path in paths)


def _sanitize_repo_path(path: str | None) -> str:
    raw_path = str(path or "").strip().replace("\\", "/")
    if not raw_path:
        return ""
    try:
        normalized = PurePosixPath(raw_path)
    except Exception:
        return ""
    if normalized.is_absolute():
        return ""
    parts = [str(part).strip() for part in normalized.parts if str(part).strip()]
    if not parts or any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _request_repo_files(task: Task, data: ClaudeRequest | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for entry in list(getattr(data, "repo_files", None) or []):
        path = _sanitize_repo_path(getattr(entry, "path", None))
        if not path:
            continue
        normalized[path] = str(getattr(entry, "content", "") or "")
    if normalized:
        return normalized
    return normalize_repo_files(getattr(task, "repo_structure", None))


def _repo_file_excerpts(task: Task, data: ClaudeRequest) -> str:
    repo_files = _request_repo_files(task, data)
    if not repo_files:
        return "No repository file excerpts available."

    selected_file_path = _sanitize_repo_path(getattr(data, "selected_file_path", None))
    ordered_paths = sorted(repo_files.keys())
    if selected_file_path and selected_file_path in repo_files:
        ordered_paths = [selected_file_path, *[path for path in ordered_paths if path != selected_file_path]]

    excerpts: list[str] = []
    remaining_chars = _MAX_CONTEXT_CHARS
    for path in ordered_paths[:_MAX_REPO_FILES_IN_PROMPT]:
        if len(excerpts) >= _MAX_REPO_FILE_EXCERPTS or remaining_chars <= 0:
            break
        content = str(repo_files.get(path) or "").strip()
        if not content:
            continue
        snippet = content[: min(_MAX_REPO_FILE_CHARS, remaining_chars)]
        if len(content) > len(snippet):
            snippet = f"{snippet}\n... [truncated]"
        block = f"=== {path} ===\n{snippet}"
        excerpts.append(block)
        remaining_chars -= len(snippet)

    if not excerpts:
        return "Repository file contents were empty."
    return "\n\n".join(excerpts)


def _build_system_prompt(task: Task, data: ClaudeRequest) -> str:
    scenario = str(getattr(task, "scenario", "") or "").strip()
    description = str(getattr(task, "description", "") or "").strip()
    task_context = scenario or description or "Task context not provided."
    repo_tree = "\n".join(f"- {path}" for path in sorted(_request_repo_files(task, data).keys())[:_MAX_REPO_FILES_IN_PROMPT]) or "No repository files available."
    repo_excerpts = _repo_file_excerpts(task, data)
    code_context = str(data.code_context or "").strip()
    if code_context:
        code_context = code_context[:_MAX_CONTEXT_CHARS]
    else:
        code_context = "No editor snapshot provided."
    selected_file_path = _sanitize_repo_path(getattr(data, "selected_file_path", None)) or "No selected file provided."

    return (
        "You are a coding assistant in TAALI's assessment workspace. "
        "Behave like a Cursor-style coding assistant: be concrete, actionable, and concise. "
        "Do not fabricate files or commands. If you reference files, use only file paths from the repository tree. "
        "Never output XML-style tags, pseudo tool calls, or internal action syntax such as <read_file>, <path>, or similar wrappers. "
        "Reply with normal human-readable guidance only.\n\n"
        f"Task context:\n{task_context}\n\n"
        f"Repository files:\n{repo_tree}\n\n"
        f"Selected file:\n{selected_file_path}\n\n"
        f"Repository file excerpts:\n{repo_excerpts}\n\n"
        f"Current editor snapshot:\n{code_context}"
    )


def _sanitize_candidate_response(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    tool_notes: list[str] = []

    def replace_tool_block(match: re.Match[str]) -> str:
        tag = str(match.group(1) or "").strip().lower()
        body = str(match.group(2) or "")
        if tag not in _INTERNAL_TOOL_TAGS:
            return match.group(0)
        if tag == "read_file":
            paths = [path.strip() for path in _PATH_TAG_RE.findall(body) if path.strip()]
            if paths:
                label = ", ".join(paths[:4])
                if len(paths) > 4:
                    label = f"{label}, +{len(paths) - 4} more"
                tool_notes.append(f"Reviewing: {label}")
        return ""

    cleaned = _TOOL_BLOCK_RE.sub(replace_tool_block, raw)
    cleaned = re.sub(r"^\s*</?[a-z_][a-z0-9_]*>\s*$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if cleaned:
        return cleaned
    if tool_notes:
        return "\n".join(tool_notes)
    return raw


def _resolve_assessment_api_key(org: Organization | None) -> str:
    env = terminal_env(org)
    return str(env.get("ANTHROPIC_API_KEY") or "").strip()


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    org = db.query(Organization).filter(Organization.id == assessment.organization_id).first()
    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    api_key = _resolve_assessment_api_key(org)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Claude API key is not configured for this workspace.",
                "requires_terminal": False,
                "terminal_capabilities": terminal_capabilities(),
            },
        )

    history = _normalize_history(data.conversation_history)
    message = str(data.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if not history or history[-1].get("role") != "user" or history[-1].get("content") != message:
        history.append({"role": "user", "content": message})

    system_prompt = _build_system_prompt(task, data)
    claude = ClaudeService(api_key)
    started_at = time.perf_counter()
    result = claude.chat(messages=history, system=system_prompt)
    latency_ms = int((time.perf_counter() - started_at) * 1000)

    if not result.get("success"):
        append_assessment_timeline_event(
            assessment,
            "ai_prompt_error",
            {"latency_ms": latency_ms},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "Claude request failed. Please retry."},
        )

    response_text = _sanitize_candidate_response(result.get("content") or "")
    input_tokens = max(0, int(result.get("input_tokens") or 0))
    output_tokens = max(0, int(result.get("output_tokens") or 0))

    prompts = list(getattr(assessment, "ai_prompts", None) or [])
    is_first_prompt = len(prompts) == 0
    prompts.append(
        {
            "message": message,
            "response": response_text,
            "code_context": str(data.code_context or "")[:_MAX_CONTEXT_CHARS],
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "time_since_assessment_start_ms": data.time_since_assessment_start_ms,
            "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
            "response_latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": utcnow().isoformat(),
        }
    )
    assessment.ai_prompts = prompts
    assessment.total_input_tokens = int(getattr(assessment, "total_input_tokens", 0) or 0) + input_tokens
    assessment.total_output_tokens = int(getattr(assessment, "total_output_tokens", 0) or 0) + output_tokens
    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "time_since_assessment_start_ms": data.time_since_assessment_start_ms,
            "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
        },
    )
    if is_first_prompt:
        append_assessment_timeline_event(
            assessment,
            "first_prompt",
            {
                "preview": message[:120],
            },
        )
    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=prompts,
    )
    db.commit()

    return {
        "success": True,
        "content": response_text,
        "response": response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": input_tokens + output_tokens,
        "latency_ms": latency_ms,
        "claude_budget": claude_budget,
    }


@router.post("/{assessment_id}/claude/retry")
def retry_claude_after_outage(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    return {
        "success": True,
        "message": "Claude is available. Submit a new prompt.",
    }
