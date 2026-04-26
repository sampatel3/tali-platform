from __future__ import annotations

import asyncio
import json
import re
import secrets
import shlex
import threading
import time

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...components.assessments.claude_budget import (
    compute_claude_cost_usd,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.terminal_runtime import (
    append_cli_transcript,
    ensure_terminal_session,
    stop_terminal_session,
    terminal_capabilities,
    terminal_mode_enabled,
    touch_terminal_session,
)
from ...domains.integrations_notifications.adapters import build_sandbox_adapter
from ...models.assessment import Assessment, AssessmentStatus
from ...models.organization import Organization
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db

router = APIRouter()


def _seed_cli_usage_from_transcript(transcript: list[dict] | None) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0
    for entry in transcript or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_usage":
            continue
        input_tokens += max(0, int(entry.get("input_tokens") or 0))
        output_tokens += max(0, int(entry.get("output_tokens") or 0))
    return input_tokens, output_tokens


TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4
_CLAUDE_CHAT_BEGIN_PREFIX = "TAALI_CLAUDE_CHAT_BEGIN "
_CLAUDE_CHAT_END_PREFIX = "TAALI_CLAUDE_CHAT_END "
_MAX_CLAUDE_CHAT_REQUEST_ID_LEN = 64
_MAX_CLAUDE_CHAT_MESSAGE_CHARS = 4000
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _estimate_token_delta(text: str, remainder_chars: int) -> tuple[int, int]:
    raw_text = str(text or "")
    if not raw_text:
        return 0, max(0, int(remainder_chars or 0))
    total_chars = max(0, int(remainder_chars or 0)) + len(raw_text.encode("utf-8"))
    tokens = total_chars // TOKEN_ESTIMATE_CHARS_PER_TOKEN
    return max(0, int(tokens)), max(0, int(total_chars % TOKEN_ESTIMATE_CHARS_PER_TOKEN))


def _sanitize_chat_request_id(value: object) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip())
    raw = raw.strip("-")
    return raw[:_MAX_CLAUDE_CHAT_REQUEST_ID_LEN]


def _sanitize_chat_selected_file(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    raw = re.sub(r"[\r\n\t]+", " ", raw)
    return raw[:500]


def _build_terminal_chat_command(*, request_id: str, message: str, selected_file_path: str = "") -> str:
    safe_request_id = _sanitize_chat_request_id(request_id)
    safe_message = str(message or "").strip()[:_MAX_CLAUDE_CHAT_MESSAGE_CHARS]
    safe_selected_file = _sanitize_chat_selected_file(selected_file_path)
    if not safe_request_id or not safe_message:
        raise ValueError("request_id and message are required")
    delimiter = f"TAALI_CLAUDE_PROMPT_{safe_request_id}_{secrets.token_hex(6)}"
    return (
        f"taali_ui_chat {shlex.quote(safe_request_id)} {shlex.quote(safe_selected_file)} <<'{delimiter}'\n"
        f"{safe_message}\n"
        f"{delimiter}\n"
    )


def _strip_ansi_sequences(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(text or ""))


def _consume_terminal_chat_output(text: str, state: dict[str, object]) -> tuple[str, list[dict[str, object]]]:
    combined = f"{state.get('line_buffer', '')}{text}"
    lines = combined.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        state["line_buffer"] = lines.pop()
    else:
        state["line_buffer"] = ""

    filtered_lines: list[str] = []
    completed: list[dict[str, object]] = []
    active_request_id = state.get("active_request_id")
    active_output = list(state.get("active_output") or [])

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped.startswith(_CLAUDE_CHAT_BEGIN_PREFIX):
            active_request_id = stripped[len(_CLAUDE_CHAT_BEGIN_PREFIX):].strip()
            active_output = []
            continue

        if active_request_id and stripped.startswith(_CLAUDE_CHAT_END_PREFIX):
            tail = stripped[len(_CLAUDE_CHAT_END_PREFIX):].strip()
            parts = tail.split(maxsplit=1)
            request_id = parts[0].strip() if parts else str(active_request_id)
            exit_status = 0
            if len(parts) > 1:
                try:
                    exit_status = int(parts[1].strip())
                except Exception:
                    exit_status = 0
            completed.append(
                {
                    "request_id": request_id,
                    "content": _strip_ansi_sequences("".join(active_output)).strip(),
                    "exit_status": exit_status,
                }
            )
            active_request_id = None
            active_output = []
            continue

        if active_request_id:
            active_output.append(line)

        filtered_lines.append(line)

    state["active_request_id"] = active_request_id
    state["active_output"] = active_output
    return "".join(filtered_lines), completed


def _extract_provider_usage(text: str) -> dict | None:
    """Extract provider-reported usage metrics from terminal output."""
    content = str(text or "")
    if not content.strip():
        return None

    # Prefer explicit tagged JSON payloads (e.g., `TAALI_CLAUDE_USAGE {"input_tokens":...}`).
    for line in content.splitlines():
        raw = line.strip()
        if not raw:
            continue
        payload_str = None
        if raw.startswith("TAALI_CLAUDE_USAGE "):
            payload_str = raw.split(" ", 1)[1].strip()
        elif raw.startswith("{") and raw.endswith("}") and (
            "input_tokens" in raw or "output_tokens" in raw or "request_cost_usd" in raw
        ):
            payload_str = raw
        if not payload_str:
            continue
        try:
            parsed = json.loads(payload_str)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        input_tokens = max(0, int(parsed.get("input_tokens") or 0))
        output_tokens = max(0, int(parsed.get("output_tokens") or 0))
        request_cost_usd = parsed.get("request_cost_usd")
        if request_cost_usd is None:
            request_cost_usd = compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "request_cost_usd": round(float(request_cost_usd), 6),
            "source": "provider_json",
        }

    # Secondary parse for plain-text provider token summaries.
    input_match = re.search(r"input[_\s-]*tokens?\s*[:=]\s*(\d+)", content, flags=re.IGNORECASE)
    output_match = re.search(r"output[_\s-]*tokens?\s*[:=]\s*(\d+)", content, flags=re.IGNORECASE)
    if not input_match and not output_match:
        return None
    input_tokens = int(input_match.group(1)) if input_match else 0
    output_tokens = int(output_match.group(1)) if output_match else 0
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "request_cost_usd": round(
            float(compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)),
            6,
        ),
        "source": "provider_text",
    }


def _cli_budget_snapshot(budget_limit_usd: float | None, input_tokens: int, output_tokens: int) -> dict:
    if budget_limit_usd is None:
        return {
            "enabled": False,
            "limit_usd": None,
            "used_usd": round(compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens), 6),
            "remaining_usd": None,
            "input_tokens_used": max(0, int(input_tokens or 0)),
            "output_tokens_used": max(0, int(output_tokens or 0)),
            "tokens_used": max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
            "is_exhausted": False,
        }

    safe_limit = max(0.0, float(budget_limit_usd))
    used = float(compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens))
    remaining = max(0.0, safe_limit - used)
    return {
        "enabled": True,
        "limit_usd": round(safe_limit, 6),
        "used_usd": round(used, 6),
        "remaining_usd": round(remaining, 6),
        "input_tokens_used": max(0, int(input_tokens or 0)),
        "output_tokens_used": max(0, int(output_tokens or 0)),
        "tokens_used": max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
        "is_exhausted": remaining <= 1e-9,
    }


def _extract_ws_token(websocket: WebSocket) -> str:
    query_token = websocket.query_params.get("token")
    if query_token:
        return query_token
    header_token = websocket.headers.get("x-assessment-token")
    return header_token or ""


async def _ws_send_json(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:
        return


@router.get("/{assessment_id}/terminal/status")
def terminal_status(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    return {
        "success": True,
        "assessment_id": assessment.id,
        "ai_mode": getattr(assessment, "ai_mode", "claude_cli_terminal"),
        "terminal_mode": getattr(assessment, "ai_mode", "claude_cli_terminal") == "claude_cli_terminal",
        "terminal_capabilities": terminal_capabilities(),
        "running": bool(getattr(assessment, "cli_session_pid", None)),
        "pid": getattr(assessment, "cli_session_pid", None),
        "state": getattr(assessment, "cli_session_state", None) or "stopped",
        "started_at": getattr(assessment, "cli_session_started_at", None),
        "last_seen_at": getattr(assessment, "cli_session_last_seen_at", None),
    }


@router.post("/{assessment_id}/terminal/stop")
def terminal_stop(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)

    pid = int(getattr(assessment, "cli_session_pid", 0) or 0)
    killed = False
    if pid > 0 and assessment.e2b_session_id:
        try:
            e2b = build_sandbox_adapter()
            sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
            killed = bool(e2b.kill_process(sandbox, pid))
        except Exception:
            killed = False

    stop_terminal_session(assessment)
    append_assessment_timeline_event(
        assessment,
        "terminal_exit",
        {"pid": pid, "reason": "manual_stop", "killed": killed},
    )
    append_cli_transcript(
        assessment,
        "terminal_exit",
        {"pid": pid, "reason": "manual_stop", "killed": killed},
    )
    db.commit()

    return {"success": True, "killed": killed, "pid": pid}


@router.websocket("/{assessment_id}/terminal/ws")
async def terminal_ws(
    websocket: WebSocket,
    assessment_id: int,
    db: Session = Depends(get_db),
):
    await websocket.accept()
    token = _extract_ws_token(websocket)
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.status == AssessmentStatus.IN_PROGRESS,
        Assessment.is_voided.is_(False),
    ).first()

    if not assessment:
        await _ws_send_json(websocket, {"type": "error", "message": "Active assessment not found"})
        await websocket.close(code=4404)
        return

    if not token or not secrets.compare_digest(assessment.token or "", token or ""):
        await _ws_send_json(websocket, {"type": "error", "message": "Invalid assessment token"})
        await websocket.close(code=4403)
        return

    if getattr(assessment, "ai_mode", "claude_cli_terminal") != "claude_cli_terminal" or not terminal_mode_enabled():
        await _ws_send_json(
            websocket,
            {
                "type": "status",
                "ai_mode": getattr(assessment, "ai_mode", "claude_cli_terminal"),
                "terminal_mode": False,
                "message": "Terminal mode is disabled for this assessment.",
            },
        )
        await websocket.close(code=4400)
        return

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        await _ws_send_json(websocket, {"type": "error", "message": "Task not found"})
        await websocket.close(code=4404)
        return

    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    input_tokens_used, output_tokens_used = _seed_cli_usage_from_transcript(
        list(getattr(assessment, "cli_transcript", None) or [])
    )
    provider_usage_events_seen = sum(
        1
        for entry in (getattr(assessment, "cli_transcript", None) or [])
        if isinstance(entry, dict) and str(entry.get("event_type") or "") == "terminal_usage"
    )
    using_provider_usage = provider_usage_events_seen > 0
    input_token_remainder_chars = 0
    output_token_remainder_chars = 0
    pending_chat_requests: dict[str, dict[str, object]] = {}
    chat_capture_state: dict[str, object] = {
        "line_buffer": "",
        "active_request_id": None,
        "active_output": [],
    }

    org = db.query(Organization).filter(Organization.id == assessment.organization_id).first()
    e2b = build_sandbox_adapter()

    try:
        session = ensure_terminal_session(
            assessment=assessment,
            task=task,
            org=org,
            db=db,
            e2b_service=e2b,
        )
    except Exception as exc:
        await _ws_send_json(websocket, {"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return

    if not session.cli_available:
        append_assessment_timeline_event(
            assessment,
            "terminal_error",
            {"pid": session.pid, "reason": "cli_unavailable"},
        )
        append_cli_transcript(
            assessment,
            "terminal_error",
            {"pid": session.pid, "message": session.error_message or "Claude CLI unavailable"},
        )
        assessment.cli_session_state = "error"
        db.commit()
        await _ws_send_json(
            websocket,
            {
                "type": "error",
                "message": session.error_message or "Claude CLI unavailable",
                "terminal_capabilities": terminal_capabilities(),
            },
        )
        await websocket.close(code=1011)
        return

    budget_snapshot = _cli_budget_snapshot(effective_budget_limit, input_tokens_used, output_tokens_used)
    if budget_snapshot["enabled"] and budget_snapshot["is_exhausted"]:
        killed = e2b.kill_process(session.sandbox, session.pid)
        append_assessment_timeline_event(
            assessment,
            "terminal_error",
            {
                "pid": session.pid,
                "reason": "budget_exhausted",
                "killed": bool(killed),
                "used_usd": budget_snapshot["used_usd"],
                "limit_usd": budget_snapshot["limit_usd"],
            },
        )
        append_cli_transcript(
            assessment,
            "terminal_error",
            {
                "pid": session.pid,
                "reason": "budget_exhausted",
                "killed": bool(killed),
                "used_usd": budget_snapshot["used_usd"],
                "limit_usd": budget_snapshot["limit_usd"],
            },
        )
        stop_terminal_session(assessment)
        db.commit()
        await _ws_send_json(
            websocket,
            {
                "type": "error",
                "message": "Claude budget limit reached for this assessment.",
                "requires_budget_top_up": True,
                "claude_budget": budget_snapshot,
            },
        )
        await websocket.close(code=4402)
        return

    await _ws_send_json(
        websocket,
        {
            "type": "ready",
            "pid": session.pid,
            "is_new": session.is_new,
            "ai_mode": "claude_cli_terminal",
            "permission_mode": settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT,
            "claude_budget": budget_snapshot,
            "terminal_capabilities": terminal_capabilities(),
        },
    )

    output_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop_pump = threading.Event()

    def _pump_output() -> None:
        if session.handle is None:
            loop.call_soon_threadsafe(output_queue.put_nowait, {"type": "exit"})
            return
        try:
            for stdout, stderr, pty in session.handle:
                if stop_pump.is_set():
                    break
                if stdout is not None:
                    loop.call_soon_threadsafe(
                        output_queue.put_nowait,
                        {"type": "output", "stream": "stdout", "data": stdout},
                    )
                if stderr is not None:
                    loop.call_soon_threadsafe(
                        output_queue.put_nowait,
                        {"type": "output", "stream": "stderr", "data": stderr},
                    )
                if pty is not None:
                    text = pty.decode("utf-8", "replace")
                    loop.call_soon_threadsafe(
                        output_queue.put_nowait,
                        {"type": "output", "stream": "pty", "data": text},
                    )
        except Exception as exc:
            loop.call_soon_threadsafe(
                output_queue.put_nowait,
                {"type": "error", "message": str(exc)},
            )
        finally:
            loop.call_soon_threadsafe(output_queue.put_nowait, {"type": "exit"})

    pump_thread = threading.Thread(target=_pump_output, daemon=True)
    pump_thread.start()

    ws_task: asyncio.Task | None = None
    out_task: asyncio.Task | None = None
    try:
        ws_task = asyncio.create_task(websocket.receive_text())
        out_task = asyncio.create_task(output_queue.get())
        while True:
            done, _pending = await asyncio.wait(
                {ws_task, out_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            should_break = False

            # Always process websocket input first so outbound PTY spam does not starve user keystrokes.
            if ws_task in done:
                raw = ws_task.result()
                try:
                    payload = json.loads(raw)
                except Exception:
                    ws_task = asyncio.create_task(websocket.receive_text())
                    payload = None

                if payload is not None:
                    msg_type = str(payload.get("type") or "").strip().lower()
                    if msg_type in {"init", "heartbeat"}:
                        try:
                            e2b.touch_sandbox(session.sandbox)
                        except Exception:
                            # Keepalive refresh is best-effort.
                            pass
                        touch_terminal_session(assessment)
                        db.commit()
                    elif msg_type == "resize":
                        rows = int(payload.get("rows") or 30)
                        cols = int(payload.get("cols") or 120)
                        rows = max(10, min(rows, 300))
                        cols = max(20, min(cols, 600))
                        e2b.resize_pty(session.sandbox, session.pid, rows=rows, cols=cols)
                    elif msg_type == "claude_prompt":
                        request_id = _sanitize_chat_request_id(payload.get("request_id"))
                        prompt_message = str(payload.get("message") or "").strip()[:_MAX_CLAUDE_CHAT_MESSAGE_CHARS]
                        selected_file_path = _sanitize_chat_selected_file(payload.get("selected_file_path"))
                        if not request_id or not prompt_message:
                            await _ws_send_json(
                                websocket,
                                {
                                    "type": "claude_chat_error",
                                    "request_id": request_id or None,
                                    "message": "Claude prompt is missing a request id or message.",
                                },
                            )
                        elif request_id in pending_chat_requests or chat_capture_state.get("active_request_id"):
                            await _ws_send_json(
                                websocket,
                                {
                                    "type": "claude_chat_error",
                                    "request_id": request_id,
                                    "message": "Claude is already working on the previous request.",
                                },
                            )
                        else:
                            try:
                                command = _build_terminal_chat_command(
                                    request_id=request_id,
                                    message=prompt_message,
                                    selected_file_path=selected_file_path,
                                )
                                e2b.send_pty_input(session.sandbox, session.pid, command)
                            except Exception as exc:
                                await _ws_send_json(
                                    websocket,
                                    {
                                        "type": "claude_chat_error",
                                        "request_id": request_id,
                                        "message": str(exc) or "Failed to send prompt to Claude CLI.",
                                    },
                                )
                            else:
                                prompts = list(getattr(assessment, "ai_prompts", None) or [])
                                pending_chat_requests[request_id] = {
                                    "message": prompt_message,
                                    "selected_file_path": selected_file_path,
                                    "code_context": str(payload.get("code_context") or "")[:12000],
                                    "paste_detected": bool(payload.get("paste_detected")),
                                    "browser_focused": bool(payload.get("browser_focused", True)),
                                    "time_since_assessment_start_ms": payload.get("time_since_assessment_start_ms"),
                                    "time_since_last_prompt_ms": payload.get("time_since_last_prompt_ms"),
                                    "input_tokens_start": input_tokens_used,
                                    "output_tokens_start": output_tokens_used,
                                    "started_at_monotonic": time.perf_counter(),
                                    "is_first_prompt": len(prompts) == 0,
                                }
                                append_cli_transcript(
                                    assessment,
                                    "claude_prompt",
                                    {
                                        "pid": session.pid,
                                        "request_id": request_id,
                                        "message": prompt_message[:500],
                                        "selected_file_path": selected_file_path,
                                    },
                                )
                                append_assessment_timeline_event(
                                    assessment,
                                    "ai_prompt_started",
                                    {
                                        "request_id": request_id,
                                        "transport": "terminal_cli",
                                        "paste_detected": bool(payload.get("paste_detected")),
                                        "browser_focused": bool(payload.get("browser_focused", True)),
                                        "time_since_assessment_start_ms": payload.get("time_since_assessment_start_ms"),
                                        "time_since_last_prompt_ms": payload.get("time_since_last_prompt_ms"),
                                    },
                                )
                                touch_terminal_session(assessment)
                                if not using_provider_usage:
                                    estimated_input_tokens, input_token_remainder_chars = _estimate_token_delta(
                                        prompt_message,
                                        input_token_remainder_chars,
                                    )
                                    if estimated_input_tokens > 0:
                                        input_tokens_used += estimated_input_tokens
                                        assessment.total_input_tokens = int(getattr(assessment, "total_input_tokens", 0) or 0) + estimated_input_tokens
                                        append_cli_transcript(
                                            assessment,
                                            "terminal_usage",
                                            {
                                                "pid": session.pid,
                                                "input_tokens": estimated_input_tokens,
                                                "output_tokens": 0,
                                                "request_cost_usd": float(
                                                    round(
                                                        compute_claude_cost_usd(
                                                            input_tokens=estimated_input_tokens,
                                                            output_tokens=0,
                                                        ),
                                                        6,
                                                    )
                                                ),
                                                "source": "estimated_chat_input",
                                            },
                                        )
                                db.commit()
                                await _ws_send_json(
                                    websocket,
                                    {
                                        "type": "claude_chat_started",
                                        "request_id": request_id,
                                    },
                                )
                                budget_snapshot = _cli_budget_snapshot(
                                    effective_budget_limit,
                                    input_tokens_used,
                                    output_tokens_used,
                                )
                                if budget_snapshot["enabled"] and budget_snapshot["is_exhausted"]:
                                    pending_chat_requests.pop(request_id, None)
                                    killed = e2b.kill_process(session.sandbox, session.pid)
                                    append_assessment_timeline_event(
                                        assessment,
                                        "terminal_exit",
                                        {
                                            "pid": session.pid,
                                            "reason": "budget_exhausted",
                                            "killed": bool(killed),
                                            "used_usd": budget_snapshot["used_usd"],
                                            "limit_usd": budget_snapshot["limit_usd"],
                                        },
                                    )
                                    append_cli_transcript(
                                        assessment,
                                        "terminal_exit",
                                        {
                                            "pid": session.pid,
                                            "reason": "budget_exhausted",
                                            "killed": bool(killed),
                                            "used_usd": budget_snapshot["used_usd"],
                                            "limit_usd": budget_snapshot["limit_usd"],
                                        },
                                    )
                                    stop_terminal_session(assessment)
                                    db.commit()
                                    await _ws_send_json(
                                        websocket,
                                        {
                                            "type": "claude_chat_error",
                                            "request_id": request_id,
                                            "message": "Claude budget limit reached for this assessment.",
                                        },
                                    )
                                    await _ws_send_json(
                                        websocket,
                                        {
                                            "type": "error",
                                            "message": "Claude budget limit reached for this assessment.",
                                            "requires_budget_top_up": True,
                                            "claude_budget": budget_snapshot,
                                        },
                                    )
                                    await _ws_send_json(
                                        websocket,
                                        {"type": "exit", "pid": session.pid, "killed": bool(killed)},
                                    )
                                    should_break = True
                    elif msg_type == "input":
                        data = str(payload.get("data") or "")
                        if data:
                            # Prevent shell escape controls (Ctrl-C/Z/D) so the terminal remains repo-scoped.
                            if not any(ctrl in data for ctrl in ("\u0003", "\u001a", "\u0004")):
                                e2b.send_pty_input(session.sandbox, session.pid, data)
                                append_assessment_timeline_event(
                                    assessment,
                                    "terminal_input",
                                    {"pid": session.pid, "chars": len(data)},
                                )
                                append_cli_transcript(
                                    assessment,
                                    "terminal_input",
                                    {
                                        "pid": session.pid,
                                        "data": data[:1000],
                                    },
                                )
                                touch_terminal_session(assessment)
                                if not using_provider_usage:
                                    estimated_input_tokens, input_token_remainder_chars = _estimate_token_delta(
                                        data,
                                        input_token_remainder_chars,
                                    )
                                    if estimated_input_tokens > 0:
                                        input_tokens_used += estimated_input_tokens
                                        assessment.total_input_tokens = int(getattr(assessment, "total_input_tokens", 0) or 0) + estimated_input_tokens
                                        append_cli_transcript(
                                            assessment,
                                            "terminal_usage",
                                            {
                                                "pid": session.pid,
                                                "input_tokens": estimated_input_tokens,
                                                "output_tokens": 0,
                                                "request_cost_usd": float(
                                                    round(
                                                        compute_claude_cost_usd(
                                                            input_tokens=estimated_input_tokens,
                                                            output_tokens=0,
                                                        ),
                                                        6,
                                                    )
                                                ),
                                                "source": "estimated_input",
                                            },
                                        )
                                db.commit()
                                budget_snapshot = _cli_budget_snapshot(
                                    effective_budget_limit,
                                    input_tokens_used,
                                    output_tokens_used,
                                )
                                if budget_snapshot["enabled"] and budget_snapshot["is_exhausted"]:
                                    killed = e2b.kill_process(session.sandbox, session.pid)
                                    append_assessment_timeline_event(
                                        assessment,
                                        "terminal_exit",
                                        {
                                            "pid": session.pid,
                                            "reason": "budget_exhausted",
                                            "killed": bool(killed),
                                            "used_usd": budget_snapshot["used_usd"],
                                            "limit_usd": budget_snapshot["limit_usd"],
                                        },
                                    )
                                    append_cli_transcript(
                                        assessment,
                                        "terminal_exit",
                                        {
                                            "pid": session.pid,
                                            "reason": "budget_exhausted",
                                            "killed": bool(killed),
                                            "used_usd": budget_snapshot["used_usd"],
                                            "limit_usd": budget_snapshot["limit_usd"],
                                        },
                                    )
                                    stop_terminal_session(assessment)
                                    db.commit()
                                    await _ws_send_json(
                                        websocket,
                                        {
                                            "type": "error",
                                            "message": "Claude budget limit reached for this assessment.",
                                            "requires_budget_top_up": True,
                                            "claude_budget": budget_snapshot,
                                        },
                                    )
                                    await _ws_send_json(
                                        websocket,
                                        {"type": "exit", "pid": session.pid, "killed": bool(killed)},
                                    )
                                    should_break = True
                    elif msg_type == "stop":
                        killed = e2b.kill_process(session.sandbox, session.pid)
                        append_assessment_timeline_event(
                            assessment,
                            "terminal_exit",
                            {"pid": session.pid, "reason": "ws_stop", "killed": bool(killed)},
                        )
                        append_cli_transcript(
                            assessment,
                            "terminal_exit",
                            {"pid": session.pid, "reason": "ws_stop", "killed": bool(killed)},
                        )
                        stop_terminal_session(assessment)
                        db.commit()
                        await _ws_send_json(
                            websocket,
                            {"type": "exit", "pid": session.pid, "killed": bool(killed)},
                        )
                        should_break = True

                if should_break:
                    break
                ws_task = asyncio.create_task(websocket.receive_text())

            if out_task in done:
                message = out_task.result()
                msg_type = message.get("type")

                if msg_type == "output":
                    output_text = str(message.get("data") or "")
                    completed_chat_events: list[dict[str, object]] = []
                    if output_text and str(message.get("stream") or "") == "pty":
                        output_text, completed_chat_events = _consume_terminal_chat_output(
                            output_text,
                            chat_capture_state,
                        )
                    if output_text:
                        append_cli_transcript(
                            assessment,
                            "terminal_output",
                            {
                                "stream": message.get("stream"),
                                "data": output_text[:4000],
                            },
                        )
                        usage = _extract_provider_usage(output_text)
                        if usage:
                            usage_input_tokens = max(0, int(usage.get("input_tokens") or 0))
                            usage_output_tokens = max(0, int(usage.get("output_tokens") or 0))
                            usage_cost_usd = float(usage.get("request_cost_usd") or 0.0)
                            provider_usage_events_seen += 1
                            using_provider_usage = True
                            input_tokens_used += usage_input_tokens
                            output_tokens_used += usage_output_tokens
                            assessment.total_input_tokens = int(getattr(assessment, "total_input_tokens", 0) or 0) + usage_input_tokens
                            assessment.total_output_tokens = int(getattr(assessment, "total_output_tokens", 0) or 0) + usage_output_tokens
                            append_cli_transcript(
                                assessment,
                                "terminal_usage",
                                {
                                    "pid": session.pid,
                                    "input_tokens": usage_input_tokens,
                                    "output_tokens": usage_output_tokens,
                                    "request_cost_usd": usage_cost_usd,
                                    "source": usage.get("source") or "provider",
                                },
                            )
                            append_assessment_timeline_event(
                                assessment,
                                "terminal_usage",
                                {
                                    "pid": session.pid,
                                    "input_tokens": usage_input_tokens,
                                    "output_tokens": usage_output_tokens,
                                    "request_cost_usd": usage_cost_usd,
                                    "source": usage.get("source") or "provider",
                                },
                            )
                        elif not using_provider_usage:
                            estimated_output_tokens, output_token_remainder_chars = _estimate_token_delta(
                                output_text,
                                output_token_remainder_chars,
                            )
                            if estimated_output_tokens > 0:
                                output_tokens_used += estimated_output_tokens
                                assessment.total_output_tokens = int(getattr(assessment, "total_output_tokens", 0) or 0) + estimated_output_tokens
                                append_cli_transcript(
                                    assessment,
                                    "terminal_usage",
                                    {
                                        "pid": session.pid,
                                        "input_tokens": 0,
                                        "output_tokens": estimated_output_tokens,
                                        "request_cost_usd": float(
                                            round(
                                                compute_claude_cost_usd(
                                                    input_tokens=0,
                                                    output_tokens=estimated_output_tokens,
                                                ),
                                                6,
                                            )
                                        ),
                                        "source": "estimated_output",
                                    },
                                )
                        touch_terminal_session(assessment)
                        db.commit()
                        budget_snapshot = _cli_budget_snapshot(
                            effective_budget_limit,
                            input_tokens_used,
                            output_tokens_used,
                        )
                        if budget_snapshot["enabled"] and budget_snapshot["is_exhausted"]:
                            killed = e2b.kill_process(session.sandbox, session.pid)
                            append_assessment_timeline_event(
                                assessment,
                                "terminal_exit",
                                {
                                    "pid": session.pid,
                                    "reason": "budget_exhausted",
                                    "killed": bool(killed),
                                    "used_usd": budget_snapshot["used_usd"],
                                    "limit_usd": budget_snapshot["limit_usd"],
                                },
                            )
                            append_cli_transcript(
                                assessment,
                                "terminal_exit",
                                {
                                    "pid": session.pid,
                                    "reason": "budget_exhausted",
                                    "killed": bool(killed),
                                    "used_usd": budget_snapshot["used_usd"],
                                    "limit_usd": budget_snapshot["limit_usd"],
                                },
                            )
                            stop_terminal_session(assessment)
                            db.commit()
                            await _ws_send_json(
                                websocket,
                                {
                                    "type": "error",
                                    "message": "Claude budget limit reached for this assessment.",
                                    "requires_budget_top_up": True,
                                    "claude_budget": budget_snapshot,
                                },
                            )
                            await _ws_send_json(
                                websocket,
                                {"type": "exit", "pid": session.pid, "killed": bool(killed)},
                            )
                            break
                    if output_text:
                        await _ws_send_json(websocket, {**message, "data": output_text})
                    for completed in completed_chat_events:
                        request_id = _sanitize_chat_request_id(completed.get("request_id"))
                        if not request_id:
                            continue
                        metadata = pending_chat_requests.pop(request_id, {})
                        latency_ms = int(
                            (time.perf_counter() - float(metadata.get("started_at_monotonic") or time.perf_counter()))
                            * 1000
                        )
                        response_text = str(completed.get("content") or "").strip()
                        exit_status = int(completed.get("exit_status") or 0)
                        if not response_text and exit_status != 0:
                            response_text = f"Claude CLI exited with status {exit_status} before returning a response."
                        elif not response_text:
                            response_text = "Claude completed without returning a visible response."

                        input_tokens = max(
                            0,
                            input_tokens_used - int(metadata.get("input_tokens_start") or 0),
                        )
                        output_tokens = max(
                            0,
                            output_tokens_used - int(metadata.get("output_tokens_start") or 0),
                        )
                        prompts = list(getattr(assessment, "ai_prompts", None) or [])
                        prompts.append(
                            {
                                "message": str(metadata.get("message") or ""),
                                "response": response_text,
                                "code_context": str(metadata.get("code_context") or "")[:12000],
                                "paste_detected": bool(metadata.get("paste_detected")),
                                "browser_focused": bool(metadata.get("browser_focused", True)),
                                "time_since_assessment_start_ms": metadata.get("time_since_assessment_start_ms"),
                                "time_since_last_prompt_ms": metadata.get("time_since_last_prompt_ms"),
                                "response_latency_ms": latency_ms,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "selected_file_path": str(metadata.get("selected_file_path") or ""),
                                "timestamp": utcnow().isoformat(),
                                "transport": "terminal_cli",
                                "exit_status": exit_status,
                            }
                        )
                        assessment.ai_prompts = prompts
                        append_assessment_timeline_event(
                            assessment,
                            "ai_prompt",
                            {
                                "latency_ms": latency_ms,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "paste_detected": bool(metadata.get("paste_detected")),
                                "browser_focused": bool(metadata.get("browser_focused", True)),
                                "time_since_assessment_start_ms": metadata.get("time_since_assessment_start_ms"),
                                "time_since_last_prompt_ms": metadata.get("time_since_last_prompt_ms"),
                                "transport": "terminal_cli",
                                "request_id": request_id,
                                "exit_status": exit_status,
                            },
                        )
                        if bool(metadata.get("is_first_prompt")):
                            append_assessment_timeline_event(
                                assessment,
                                "first_prompt",
                                {
                                    "preview": str(metadata.get("message") or "")[:120],
                                },
                            )
                        db.commit()
                        budget_snapshot = _cli_budget_snapshot(
                            effective_budget_limit,
                            input_tokens_used,
                            output_tokens_used,
                        )
                        await _ws_send_json(
                            websocket,
                            {
                                "type": "claude_chat_done",
                                "request_id": request_id,
                                "content": response_text,
                                "exit_status": exit_status,
                                "latency_ms": latency_ms,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "tokens_used": input_tokens + output_tokens,
                                "claude_budget": budget_snapshot,
                            },
                        )
                    out_task = asyncio.create_task(output_queue.get())
                    continue

                if msg_type == "error":
                    active_request_id = _sanitize_chat_request_id(chat_capture_state.get("active_request_id"))
                    if active_request_id and active_request_id in pending_chat_requests:
                        pending_chat_requests.pop(active_request_id, None)
                        await _ws_send_json(
                            websocket,
                            {
                                "type": "claude_chat_error",
                                "request_id": active_request_id,
                                "message": str(message.get("message") or "Claude CLI encountered an error."),
                            },
                        )
                    append_assessment_timeline_event(
                        assessment,
                        "terminal_error",
                        {"pid": session.pid, "message": message.get("message")},
                    )
                    append_cli_transcript(
                        assessment,
                        "terminal_error",
                        {"pid": session.pid, "message": message.get("message")},
                    )
                    assessment.cli_session_state = "error"
                    db.commit()
                    await _ws_send_json(websocket, message)
                    out_task = asyncio.create_task(output_queue.get())
                    continue

                if msg_type == "exit":
                    active_request_id = _sanitize_chat_request_id(chat_capture_state.get("active_request_id"))
                    if active_request_id and active_request_id in pending_chat_requests:
                        pending_chat_requests.pop(active_request_id, None)
                        await _ws_send_json(
                            websocket,
                            {
                                "type": "claude_chat_error",
                                "request_id": active_request_id,
                                "message": "Claude CLI exited before the request completed.",
                            },
                        )
                    append_assessment_timeline_event(
                        assessment,
                        "terminal_exit",
                        {"pid": session.pid, "reason": "process_exit"},
                    )
                    append_cli_transcript(
                        assessment,
                        "terminal_exit",
                        {"pid": session.pid, "reason": "process_exit"},
                    )
                    stop_terminal_session(assessment)
                    db.commit()
                    await _ws_send_json(websocket, {"type": "exit", "pid": session.pid})
                    break
                out_task = asyncio.create_task(output_queue.get())
    except WebSocketDisconnect:
        touch_terminal_session(assessment)
        db.commit()
    finally:
        for task_item in (ws_task, out_task):
            if task_item is not None and not task_item.done():
                task_item.cancel()
        stop_pump.set()
        if session.handle is not None:
            try:
                session.handle.disconnect()
            except Exception:
                pass
