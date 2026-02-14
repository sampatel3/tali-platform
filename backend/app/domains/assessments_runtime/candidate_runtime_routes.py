from __future__ import annotations

import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
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
DEMO_TRACK_TASKS = {
    "backend-reliability": {
        "task_key": "taali_demo_backend_reliability",
        "name": "TAALI Demo: Backend API Reliability",
        "description": "Stabilize a flaky API endpoint and ship a safe patch with validation.",
        "task_type": "python",
        "difficulty": "medium",
        "duration_minutes": 25,
        "role": "ai_engineer",
        "scenario": (
            "An order sync endpoint occasionally duplicates records in production. "
            "Patch the issue, explain the root cause, and add a regression test."
        ),
        "repo_structure": {
            "files": {
                "src/order_merge.py": (
                    "def merge_order(existing, incoming):\n"
                    "    \"\"\"Merge incoming payload into an existing order record.\"\"\"\n"
                    "    if incoming.get(\"status\"):\n"
                    "        existing[\"status\"] = incoming[\"status\"]\n"
                    "    if incoming.get(\"items\"):\n"
                    "        existing[\"items\"] += incoming[\"items\"]\n"
                    "    return existing\n"
                ),
                "tests/test_order_merge.py": (
                    "from src.order_merge import merge_order\n\n"
                    "def test_merge_status():\n"
                    "    existing = {\"status\": \"open\", \"items\": []}\n"
                    "    incoming = {\"status\": \"closed\"}\n"
                    "    assert merge_order(existing, incoming)[\"status\"] == \"closed\"\n"
                ),
            },
        },
    },
    "frontend-debugging": {
        "task_key": "taali_demo_frontend_debugging",
        "name": "TAALI Demo: Frontend Bug Triage",
        "description": "Investigate state overwrite issues and apply a robust fix.",
        "task_type": "javascript",
        "difficulty": "medium",
        "duration_minutes": 20,
        "role": "frontend_engineer",
        "scenario": (
            "A settings form resets local edits after slow API responses. "
            "Fix stale response handling and explain your approach."
        ),
        "repo_structure": {
            "files": {
                "src/settingsMerge.js": (
                    "export function mergeRemoteSettings(localDraft, remoteData) {\n"
                    "  return { ...localDraft, ...remoteData };\n"
                    "}\n"
                ),
                "src/hooks/useSettingsSync.js": (
                    "export function shouldApplyServerPayload(lastEditedAt, payloadFetchedAt) {\n"
                    "  return payloadFetchedAt >= lastEditedAt;\n"
                    "}\n"
                ),
            },
        },
    },
    "data-pipeline": {
        "task_key": "taali_demo_data_pipeline",
        "name": "TAALI Demo: Data Pipeline Incident",
        "description": "Trace a transformation bug and restore safe downstream output.",
        "task_type": "python",
        "difficulty": "hard",
        "duration_minutes": 30,
        "role": "data_engineer",
        "scenario": (
            "A daily ETL run is dropping qualifying records. "
            "Identify the transformation bug, patch it, and add validation coverage."
        ),
        "repo_structure": {
            "files": {
                "pipeline/transform.py": (
                    "def normalize_record(record):\n"
                    "    score = int(record.get(\"score\", 0))\n"
                    "    if score < 50:\n"
                    "        return None\n"
                    "    record[\"score\"] = score\n"
                    "    return record\n"
                ),
                "pipeline/tests/test_transform.py": (
                    "from pipeline.transform import normalize_record\n\n"
                    "def test_preserves_qualifying_rows():\n"
                    "    assert normalize_record({\"score\": \"65\"})[\"score\"] == 65\n"
                ),
            },
        },
    },
}
DEMO_TRACK_KEYS = set(DEMO_TRACK_TASKS.keys())


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
    track_def = DEMO_TRACK_TASKS.get(track)
    if not track_def:
        return None

    task_key = track_def["task_key"]
    task = (
        db.query(Task)
        .filter(
            Task.is_active == True,  # noqa: E712
            Task.task_key == task_key,
            Task.organization_id == org_id,
        )
        .order_by(Task.id.asc())
        .first()
    )
    if task:
        return task

    task = (
        db.query(Task)
        .filter(
            Task.is_active == True,  # noqa: E712
            Task.task_key == task_key,
            Task.organization_id == None,  # noqa: E711
        )
        .order_by(Task.id.asc())
        .first()
    )
    if task:
        return task

    evaluation_rubric = {
        "task_completion": {"weight": 0.3},
        "prompt_clarity": {"weight": 0.2},
        "context_provision": {"weight": 0.2},
        "independence_efficiency": {"weight": 0.2},
        "written_communication": {"weight": 0.1},
    }
    task = Task(
        organization_id=org_id,
        name=track_def["name"],
        description=track_def["description"],
        task_type=track_def["task_type"],
        difficulty=track_def["difficulty"],
        duration_minutes=track_def["duration_minutes"],
        starter_code="",
        test_code="",
        task_key=task_key,
        role=track_def["role"],
        scenario=track_def["scenario"],
        repo_structure=track_def["repo_structure"],
        evaluation_rubric=evaluation_rubric,
        extra_data={"demo_track": track},
        is_active=True,
    )
    db.add(task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return None
    db.refresh(task)
    return task


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
