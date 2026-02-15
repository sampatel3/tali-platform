from __future__ import annotations

import secrets
import time
import json
import asyncio
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    compute_claude_cost_usd,
)
from ...components.assessments.repository import (
    append_assessment_timeline_event,
    ensure_utc,
    get_active_assessment,
    time_remaining_seconds,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.service import (
    enforce_active_or_timeout,
    enforce_not_paused,
    resume_assessment_timer,
    start_or_resume_assessment,
    store_cv_upload,
    submit_assessment as _submit_assessment,
)
from ...components.assessments.terminal_runtime import (
    append_cli_transcript,
    ensure_terminal_session,
    stop_terminal_session,
    terminal_capabilities,
    terminal_mode_enabled,
    touch_terminal_session,
)
from ...domains.integrations_notifications.adapters import (
    build_claude_adapter,
    build_sandbox_adapter,
)
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.organization import Organization
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.assessment import (
    AssessmentStart,
    ClaudeRequest,
    CodeExecutionRequest,
    DemoAssessmentStartRequest,
    SubmitRequest,
)

router = APIRouter()


DEMO_ORG_SLUG = "taali-demo"
DEMO_ORG_NAME = "TAALI Demo Leads"
DEMO_TRACK_TASK_KEYS = {
    "backend-reliability": "taali_demo_backend_reliability",
    "frontend-debugging": "taali_demo_frontend_debugging",
    "data-pipeline": "taali_demo_data_pipeline",
}
DEMO_TRACK_KEYS = {
    "backend-reliability",
    "frontend-debugging",
    "data-pipeline",
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


def _ensure_demo_org(db: Session) -> Organization:
    org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
    if org:
        return org

    org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG, plan="pay_per_use")
    db.add(org)
    try:
        db.commit()
    except Exception:
        db.rollback()
        org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
        if org:
            return org
        raise HTTPException(status_code=500, detail="Failed to initialize demo organization")

    db.refresh(org)
    return org


def _resolve_demo_task(db: Session, org_id: int, track: str) -> Task | None:
    task_key = DEMO_TRACK_TASK_KEYS.get(track)
    if task_key:
        org_task = (
            db.query(Task)
            .filter(
                Task.is_active == True,  # noqa: E712
                Task.organization_id == org_id,
                Task.task_key == task_key,
            )
            .order_by(Task.id.asc())
            .first()
        )
        if org_task:
            return org_task

        global_task = (
            db.query(Task)
            .filter(
                Task.is_active == True,  # noqa: E712
                Task.organization_id == None,  # noqa: E711
                Task.task_key == task_key,
            )
            .order_by(Task.id.asc())
            .first()
        )
        if global_task:
            return global_task

    # Backward-compatible fallback: if keyed demo tasks are not yet seeded,
    # continue to use the first active task visible to the demo org.
    fallback_task = (
        db.query(Task)
        .filter(
            Task.is_active == True,  # noqa: E712
            ((Task.organization_id == None) | (Task.organization_id == org_id)),  # noqa: E711
        )
        .order_by(Task.id.asc())
        .first()
    )
    if fallback_task:
        return fallback_task

    seeded_task = Task(
        organization_id=org_id,
        name="TAALI Demo Assessment",
        description="Debug and improve a small code path while explaining tradeoffs.",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code=(
            "def normalize_items(items):\n"
            "    normalized = []\n"
            "    for item in items:\n"
            "        normalized.append(item.strip().lower())\n"
            "    return normalized\n"
        ),
        test_code="",
        task_key=task_key or f"taali_demo_{track.replace('-', '_')}",
        role="ai_engineer",
        scenario=(
            "A production ingestion step is creating duplicate, inconsistent records. "
            "Tighten normalization logic and explain how you validated the fix."
        ),
        repo_structure={
            "files": {
                "main.py": (
                    "def normalize_items(items):\n"
                    "    normalized = []\n"
                    "    for item in items:\n"
                    "        normalized.append(item.strip().lower())\n"
                    "    return normalized\n"
                ),
                "README.md": (
                    "# TAALI Demo Assessment\n\n"
                    "- Stabilize normalization behavior.\n"
                    "- Prevent duplicate output rows.\n"
                    "- Explain validation strategy.\n"
                ),
            },
        },
        evaluation_rubric={
            "task_completion": {"weight": 0.3},
            "prompt_clarity": {"weight": 0.2},
            "context_provision": {"weight": 0.2},
            "independence_efficiency": {"weight": 0.2},
            "written_communication": {"weight": 0.1},
        },
        is_active=True,
    )
    db.add(seeded_task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return None
    db.refresh(seeded_task)
    return seeded_task


@router.post("/token/{token}/start", response_model=AssessmentStart)
def start_assessment(token: str, db: Session = Depends(get_db)):
    """Candidate starts or resumes an assessment via token."""
    assessment = db.query(Assessment).filter(Assessment.token == token).with_for_update().first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    return start_or_resume_assessment(assessment, db)


@router.post("/demo/start", response_model=AssessmentStart)
def start_demo_assessment(
    data: DemoAssessmentStartRequest,
    db: Session = Depends(get_db),
):
    """Create a demo lead + assessment and start the normal runtime session."""
    track = str(data.assessment_track or "").strip().lower()
    if track not in DEMO_TRACK_KEYS:
        raise HTTPException(status_code=400, detail="Unsupported demo assessment track")

    org = _ensure_demo_org(db)
    task = _resolve_demo_task(db, org.id, track)
    if not task:
        raise HTTPException(status_code=503, detail="No demo assessment task is available yet")

    normalized_email = str(data.email).strip().lower()
    normalized_work_email = str(data.work_email).strip().lower() if data.work_email else None

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org.id,
            Candidate.email == normalized_email,
        )
        .first()
    )
    if not candidate:
        candidate = Candidate(
            organization_id=org.id,
            email=normalized_email,
        )
        db.add(candidate)
        db.flush()

    candidate.full_name = data.full_name
    candidate.position = data.position
    candidate.work_email = normalized_work_email
    candidate.company_name = data.company_name
    candidate.company_size = data.company_size
    candidate.lead_source = "landing_demo"
    candidate.marketing_consent = bool(data.marketing_consent)
    candidate.workable_data = {
        **(candidate.workable_data or {}),
        "demo_track": track,
        "marketing_consent": bool(data.marketing_consent),
    }

    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=secrets.token_urlsafe(32),
        duration_minutes=task.duration_minutes or 30,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        is_demo=True,
        demo_track=track,
        demo_profile={
            "full_name": data.full_name,
            "position": data.position,
            "email": normalized_email,
            "work_email": normalized_work_email,
            "company_name": data.company_name,
            "company_size": data.company_size,
            "marketing_consent": bool(data.marketing_consent),
            "lead_source": "landing_demo",
        },
    )
    db.add(assessment)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create demo assessment")

    db.refresh(assessment)
    return start_or_resume_assessment(assessment, db)


@router.post("/{assessment_id}/upload-cv")
def upload_assessment_cv(
    assessment_id: int,
    file: UploadFile = File(...),
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not secrets.compare_digest(assessment.token or "", token or ""):
        raise HTTPException(status_code=401, detail="Invalid assessment token")
    if assessment.status == AssessmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Assessment already submitted")
    enforce_not_paused(assessment)
    return store_cv_upload(assessment, file, db)


@router.post("/token/{token}/upload-cv")
def upload_assessment_cv_by_token(
    token: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if assessment.status == AssessmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Assessment already submitted")
    enforce_not_paused(assessment)
    return store_cv_upload(assessment, file, db)


@router.post("/{assessment_id}/execute")
def execute_code(
    assessment_id: int,
    data: CodeExecutionRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Execute code in the assessment's E2B sandbox."""
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)

    e2b = build_sandbox_adapter()
    if assessment.e2b_session_id:
        try:
            sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
        except Exception:
            sandbox = e2b.create_sandbox()
    else:
        sandbox = e2b.create_sandbox()
        assessment.e2b_session_id = e2b.get_sandbox_id(sandbox)
        try:
            db.commit()
        except Exception:
            db.rollback()
    t0 = time.time()
    result = e2b.execute_code(sandbox, data.code)
    exec_latency_ms = int((time.time() - t0) * 1000)

    append_assessment_timeline_event(
        assessment,
        "code_execute",
        {
            "session_id": assessment.e2b_session_id,
            "code_length": len(data.code or ""),
            "latency_ms": exec_latency_ms,
            "has_stderr": bool(result.get("stderr")),
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
    return result


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Send a message to Claude AI assistant during assessment."""
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if getattr(assessment, "ai_mode", "legacy_chat") == "claude_cli_terminal":
        return {
            "success": False,
            "response": "This assessment uses Claude Code CLI in the terminal pane. Use the terminal instead of chat.",
            "content": "",
            "message": "This assessment uses Claude Code CLI in the terminal pane. Use the terminal instead of chat.",
            "is_timer_paused": bool(getattr(assessment, "is_timer_paused", False)),
            "pause_reason": getattr(assessment, "pause_reason", None),
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_terminal": True,
            "ai_mode": getattr(assessment, "ai_mode", "legacy_chat"),
            "terminal_capabilities": terminal_capabilities(),
            "claude_budget": build_claude_budget_snapshot(
                budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
                prompts=assessment.ai_prompts or [],
            ),
            "budget_exhausted": False,
        }

    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
        prompts=assessment.ai_prompts or [],
    )
    if claude_budget["enabled"] and claude_budget["is_exhausted"]:
        append_assessment_timeline_event(
            assessment,
            "ai_prompt_blocked_budget",
            {
                "used_usd": claude_budget["used_usd"],
                "limit_usd": claude_budget["limit_usd"],
                "tokens_used": claude_budget["tokens_used"],
            },
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {
            "success": False,
            "response": "Claude budget limit reached for this task. Continue coding and submit when ready.",
            "content": "",
            "message": "Claude budget limit reached for this task. Continue coding and submit when ready.",
            "is_timer_paused": False,
            "pause_reason": None,
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_budget_top_up": True,
            "claude_budget": claude_budget,
            "budget_exhausted": True,
        }

    claude = build_claude_adapter()
    messages = data.conversation_history + [{"role": "user", "content": data.message}]

    t0 = time.time()
    response = claude.chat(messages)
    latency_ms = int((time.time() - t0) * 1000)
    claude_success = bool(response.get("success"))
    claude_text = (response.get("content", "") if claude_success else "") or ""
    input_tokens = max(0, int(response.get("input_tokens", 0) or 0))
    output_tokens = max(0, int(response.get("output_tokens", 0) or 0))
    tokens_used = max(0, int(response.get("tokens_used", 0) or 0))
    request_cost_usd = compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)

    prompt_record = {
        "message": data.message,
        "response": claude_text,
        "success": claude_success,
        "claude_outage": not claude_success,
        "timestamp": utcnow().isoformat(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": tokens_used,
        "request_cost_usd": round(request_cost_usd, 6),
        "response_latency_ms": latency_ms,
        "code_before": data.code_context or "",
        "code_after": "",
        "word_count": len(data.message.split()),
        "char_count": len(data.message),
        "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
        "paste_detected": data.paste_detected,
        "browser_focused": data.browser_focused,
    }

    if assessment.ai_prompts is None:
        assessment.ai_prompts = []

    prompts = list(assessment.ai_prompts)

    if prompts and data.code_context:
        prompts[-1] = {**prompts[-1], "code_after": data.code_context}

    prompts.append(prompt_record)
    updated_budget = build_claude_budget_snapshot(
        budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
        prompts=prompts,
    )
    prompts[-1] = {
        **prompts[-1],
        "claude_budget_used_usd": updated_budget["used_usd"],
        "claude_budget_remaining_usd": updated_budget["remaining_usd"],
    }
    assessment.ai_prompts = prompts

    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "word_count": prompt_record["word_count"],
            "char_count": prompt_record["char_count"],
            "input_tokens": prompt_record["input_tokens"],
            "output_tokens": prompt_record["output_tokens"],
            "response_latency_ms": prompt_record["response_latency_ms"],
            "paste_detected": prompt_record["paste_detected"],
            "browser_focused": prompt_record["browser_focused"],
            "time_since_last_prompt_ms": prompt_record["time_since_last_prompt_ms"],
            "request_cost_usd": round(request_cost_usd, 6),
            "claude_budget_used_usd": updated_budget["used_usd"],
            "claude_budget_remaining_usd": updated_budget["remaining_usd"],
            "claude_outage": not claude_success,
        },
    )

    if len(prompts) == 1 and assessment.started_at:
        started = ensure_utc(assessment.started_at)
        assessment.time_to_first_prompt_seconds = int((utcnow() - started).total_seconds())

    if not claude_success and not assessment.is_timer_paused:
        assessment.is_timer_paused = True
        assessment.paused_at = utcnow()
        assessment.pause_reason = "claude_outage"
        append_assessment_timeline_event(
            assessment,
            "timer_paused",
            {"pause_reason": "claude_outage"},
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to persist AI interaction")

    if not claude_success:
        return {
            "success": False,
            "response": "Claude is temporarily unavailable. Your timer is paused. Please retry in a moment.",
            "content": "",
            "message": "Claude is temporarily unavailable. Your timer is paused. Please retry in a moment.",
            "is_timer_paused": True,
            "pause_reason": assessment.pause_reason,
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_retry": True,
            "claude_budget": updated_budget,
            "budget_exhausted": bool(updated_budget["enabled"] and updated_budget["is_exhausted"]),
        }

    return {
        "success": True,
        "response": claude_text,
        "content": claude_text,
        "message": claude_text,
        "tokens_used": tokens_used,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "request_cost_usd": round(request_cost_usd, 6),
        "is_timer_paused": False,
        "pause_reason": None,
        "time_remaining_seconds": time_remaining_seconds(assessment),
        "claude_budget": updated_budget,
        "budget_exhausted": bool(updated_budget["enabled"] and updated_budget["is_exhausted"]),
    }


@router.post("/{assessment_id}/claude/retry")
def retry_claude_after_outage(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)

    if not assessment.is_timer_paused:
        return {
            "success": True,
            "message": "Assessment is not paused",
            "is_timer_paused": False,
            "time_remaining_seconds": time_remaining_seconds(assessment),
        }

    claude = build_claude_adapter()
    health = claude.chat(
        messages=[{"role": "user", "content": "Reply with OK."}],
        system="Reply with the single word OK.",
    )
    if not health.get("success"):
        return {
            "success": False,
            "message": "Claude is still unavailable",
            "is_timer_paused": True,
            "pause_reason": assessment.pause_reason,
            "time_remaining_seconds": time_remaining_seconds(assessment),
        }

    resume_assessment_timer(assessment, db, resume_reason="claude_retry_success")
    db.refresh(assessment)
    return {
        "success": True,
        "message": "Claude recovered and assessment resumed",
        "is_timer_paused": False,
        "time_remaining_seconds": time_remaining_seconds(assessment),
    }


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

    await _ws_send_json(
        websocket,
        {
            "type": "ready",
            "pid": session.pid,
            "is_new": session.is_new,
            "ai_mode": "claude_cli_terminal",
            "permission_mode": settings.CLAUDE_CLI_PERMISSION_MODE_DEFAULT,
            "terminal_capabilities": terminal_capabilities(),
        },
    )

    output_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop_pump = threading.Event()

    def _pump_output() -> None:
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
                        append_cli_transcript(
                            assessment,
                            "terminal_output",
                            {
                                "stream": message.get("stream"),
                                "data": output_text[:4000],
                            },
                        )
                        touch_terminal_session(assessment)
                        db.commit()
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
                    await _ws_send_json(
                        websocket,
                        {
                            "type": "status",
                            "pid": session.pid,
                            "state": assessment.cli_session_state or "running",
                            "ai_mode": "claude_cli_terminal",
                        },
                    )
                    continue

                if msg_type == "heartbeat":
                    touch_terminal_session(assessment)
                    db.commit()
                    await _ws_send_json(
                        websocket,
                        {
                            "type": "status",
                            "pid": session.pid,
                            "state": assessment.cli_session_state or "running",
                        },
                    )
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
                    e2b.send_pty_input(session.sandbox, session.pid, data)
                    append_assessment_timeline_event(
                        assessment,
                        "terminal_input",
                        {"pid": session.pid, "chars": len(data)},
                    )
                    append_cli_transcript(
                        assessment,
                        "terminal_input",
                        {"pid": session.pid, "data": data[:1000]},
                    )
                    touch_terminal_session(assessment)
                    db.commit()
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
        try:
            session.handle.disconnect()
        except Exception:
            pass


@router.post("/{assessment_id}/submit")
def submit_assessment_endpoint(
    assessment_id: int,
    data: SubmitRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Submit the assessment, run tests, and calculate composite score."""
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    enforce_not_paused(assessment)
    return _submit_assessment(assessment, data.final_code, data.tab_switch_count, db)
