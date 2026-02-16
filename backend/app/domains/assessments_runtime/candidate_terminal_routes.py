from __future__ import annotations

import asyncio
import json
import math
import secrets
import threading

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...components.assessments.claude_budget import (
    compute_claude_cost_usd,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
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


def _estimate_tokens_from_text(text: str) -> int:
    chars = len(text or "")
    if chars <= 0:
        return 0
    chars_per_token = max(1.0, float(getattr(settings, "CLAUDE_CLI_CHARS_PER_TOKEN_ESTIMATE", 4.0) or 4.0))
    return max(1, int(math.ceil(chars / chars_per_token)))


def _seed_cli_usage_from_transcript(transcript: list[dict] | None) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0
    for entry in transcript or []:
        if not isinstance(entry, dict):
            continue
        event_type = str(entry.get("event_type") or "")
        token_estimate = max(0, int(entry.get("token_estimate") or 0))
        text = str(entry.get("data") or "")
        if event_type == "terminal_input":
            input_tokens += token_estimate if token_estimate > 0 else _estimate_tokens_from_text(text)
        elif event_type == "terminal_output":
            output_tokens += token_estimate if token_estimate > 0 else _estimate_tokens_from_text(text)
    return input_tokens, output_tokens


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
        "ai_mode": getattr(assessment, "ai_mode", "legacy_chat"),
        "terminal_mode": getattr(assessment, "ai_mode", "legacy_chat") == "claude_cli_terminal",
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
    ).first()

    if not assessment:
        await _ws_send_json(websocket, {"type": "error", "message": "Active assessment not found"})
        await websocket.close(code=4404)
        return

    if not token or not secrets.compare_digest(assessment.token or "", token or ""):
        await _ws_send_json(websocket, {"type": "error", "message": "Invalid assessment token"})
        await websocket.close(code=4403)
        return

    if getattr(assessment, "ai_mode", "legacy_chat") != "claude_cli_terminal" or not terminal_mode_enabled():
        await _ws_send_json(
            websocket,
            {
                "type": "status",
                "ai_mode": getattr(assessment, "ai_mode", "legacy_chat"),
                "terminal_mode": False,
                "fallback_chat": True,
                "message": "Terminal mode is disabled for this assessment. Use legacy chat.",
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
                "fallback_chat": True,
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
                "fallback_chat": True,
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

    try:
        while True:
            ws_task = asyncio.create_task(websocket.receive_text())
            out_task = asyncio.create_task(output_queue.get())
            done, pending = await asyncio.wait(
                {ws_task, out_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task_item in pending:
                task_item.cancel()

            if out_task in done:
                message = out_task.result()
                msg_type = message.get("type")

                if msg_type == "output":
                    output_text = str(message.get("data") or "")
                    if output_text:
                        output_token_estimate = _estimate_tokens_from_text(output_text)
                        output_tokens_used += output_token_estimate
                        append_cli_transcript(
                            assessment,
                            "terminal_output",
                            {
                                "stream": message.get("stream"),
                                "data": output_text[:4000],
                                "token_estimate": output_token_estimate,
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
                                    "fallback_chat": True,
                                },
                            )
                            await _ws_send_json(websocket, {"type": "exit", "pid": session.pid, "killed": bool(killed)})
                            break
                    await _ws_send_json(websocket, message)
                    continue

                if msg_type == "error":
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
                    continue

                if msg_type == "exit":
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

            if ws_task in done:
                raw = ws_task.result()
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue

                msg_type = str(payload.get("type") or "").strip().lower()
                if msg_type == "init":
                    touch_terminal_session(assessment)
                    db.commit()
                    continue

                if msg_type == "heartbeat":
                    touch_terminal_session(assessment)
                    db.commit()
                    continue

                if msg_type == "resize":
                    rows = int(payload.get("rows") or 30)
                    cols = int(payload.get("cols") or 120)
                    rows = max(10, min(rows, 300))
                    cols = max(20, min(cols, 600))
                    e2b.resize_pty(session.sandbox, session.pid, rows=rows, cols=cols)
                    continue

                if msg_type == "input":
                    data = str(payload.get("data") or "")
                    if not data:
                        continue
                    # Prevent shell escape controls (Ctrl-C/Z/D) so the terminal remains repo-scoped.
                    if any(ctrl in data for ctrl in ("\u0003", "\u001a", "\u0004")):
                        continue
                    e2b.send_pty_input(session.sandbox, session.pid, data)
                    input_token_estimate = _estimate_tokens_from_text(data)
                    input_tokens_used += input_token_estimate
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
                            "token_estimate": input_token_estimate,
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
                                "fallback_chat": True,
                            },
                        )
                        await _ws_send_json(websocket, {"type": "exit", "pid": session.pid, "killed": bool(killed)})
                        break
                    continue

                if msg_type == "stop":
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
                    await _ws_send_json(websocket, {"type": "exit", "pid": session.pid, "killed": bool(killed)})
                    break
    except WebSocketDisconnect:
        touch_terminal_session(assessment)
        db.commit()
    finally:
        stop_pump.set()
        if session.handle is not None:
            try:
                session.handle.disconnect()
            except Exception:
                pass
