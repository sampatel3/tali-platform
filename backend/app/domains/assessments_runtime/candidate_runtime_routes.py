from __future__ import annotations

import json
import logging
import re
import secrets
import shlex
import time
from datetime import timedelta
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    ensure_utc,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.service import (
    CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
    _clone_assessment_branch_into_workspace,
    _is_demo_workspace_fallback_enabled,
    _materialize_task_repository,
    _run_workspace_bootstrap,
    _workspace_repo_root,
    enforce_active_or_timeout,
    enforce_not_paused,
    get_assessment_start_gate,
    start_or_resume_assessment,
    store_cv_upload,
    submit_assessment as _submit_assessment,
)
from ...components.assessments.terminal_runtime import terminal_capabilities
from ...domains.integrations_notifications.adapters import (
    build_sandbox_adapter,
)
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db
from ...services.candidate_feedback_engine import (
    build_feedback_text_report,
    build_plain_text_pdf,
)
from ...services.task_repo_service import normalize_repo_files
from ...services.task_spec_loader import candidate_rubric_view
from ...schemas.assessment import (
    AssessmentStart,
    AssessmentStartRequest,
    CodeExecutionRequest,
    DemoBookingRequest,
    DemoBookingResponse,
    DemoAssessmentStartRequest,
    RepoFileSaveRequest,
    SubmitRequest,
)

from .candidate_claude_routes import router as candidate_claude_router
from .candidate_terminal_routes import router as candidate_terminal_router

router = APIRouter()
router.include_router(candidate_claude_router)
router.include_router(candidate_terminal_router)

logger = logging.getLogger(__name__)


DEMO_ORG_SLUG = "taali-demo"
DEMO_ORG_NAME = "TAALI Demo Leads"
DEMO_TRACK_TASK_KEYS = {
    # Primary demo track: canonical tasks for product demos.
    "data_eng_aws_glue_pipeline_recovery": "data_eng_aws_glue_pipeline_recovery",
    "ai_eng_genai_production_readiness": "ai_eng_genai_production_readiness",
    # Backward-compatible aliases (route to current tasks; legacy keys removed from repo).
    "data_eng_super_platform_crisis": "data_eng_aws_glue_pipeline_recovery",
    "ai_eng_super_production_launch": "ai_eng_genai_production_readiness",
    "data_eng_a_pipeline_reliability": "data_eng_aws_glue_pipeline_recovery",
    "data_eng_b_cdc_fix": "data_eng_aws_glue_pipeline_recovery",
    "data_eng_c_backfill_schema": "data_eng_aws_glue_pipeline_recovery",
    "backend-reliability": "data_eng_aws_glue_pipeline_recovery",
    "frontend-debugging": "data_eng_aws_glue_pipeline_recovery",
    "data-pipeline": "data_eng_aws_glue_pipeline_recovery",
}
DEMO_TRACK_KEYS = set(DEMO_TRACK_TASK_KEYS.keys())
_MAX_RUNTIME_REPO_FILES = 200
_PYTHON_MODULE_PART_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _preview_repo_structure(repo_structure: dict | None) -> dict | None:
    files = normalize_repo_files(repo_structure)
    if not files:
        return None
    return {
        "files": {path: "" for path in files.keys()},
    }


def _execution_stdout_text(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("stdout") or result.get("out") or "")

    logs = getattr(result, "logs", None)
    raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
    if isinstance(raw_stdout, list):
        return "\n".join(str(item) for item in raw_stdout)
    if raw_stdout is not None:
        return str(raw_stdout)
    return str(getattr(result, "stdout", "") or "")


def _extract_process_output(result: object) -> tuple[str, str, int | None]:
    if isinstance(result, dict):
        stdout = str(result.get("stdout") or result.get("out") or "")
        stderr = str(result.get("stderr") or result.get("err") or "")
        exit_code = result.get("exit_code")
        try:
            return stdout, stderr, int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            return stdout, stderr, None

    stdout = str(getattr(result, "stdout", "") or getattr(result, "out", "") or "")
    stderr = str(getattr(result, "stderr", "") or getattr(result, "err", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return stdout, stderr, exit_code


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


def _task_extra_data(task: Task) -> dict:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def _normalize_runtime_repo_files(entries: list[object] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for entry in list(entries or [])[:_MAX_RUNTIME_REPO_FILES]:
        path = _sanitize_repo_path(getattr(entry, "path", None))
        if not path:
            continue
        normalized[path] = str(getattr(entry, "content", "") or "")
    return normalized


def _sandbox_repo_exists(sandbox: object, repo_root: str) -> bool:
    result = sandbox.run_code(
        "import json, pathlib\n"
        f"repo_root = pathlib.Path({repo_root!r})\n"
        "print(json.dumps({'exists': repo_root.exists(), 'is_dir': repo_root.is_dir()}))\n"
    )
    try:
        lines = _execution_stdout_text(result).strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
    except Exception:
        logger.exception("Failed to inspect sandbox repo root=%s", repo_root)
        return False
    return bool(payload.get("exists")) and bool(payload.get("is_dir"))


def _ensure_assessment_workspace_ready(e2b: object, sandbox: object, assessment: Assessment, task: Task) -> str:
    repo_root = _workspace_repo_root(task)
    if _sandbox_repo_exists(sandbox, repo_root):
        return repo_root

    cloned = _clone_assessment_branch_into_workspace(sandbox, assessment, task)
    if not cloned and _is_demo_workspace_fallback_enabled(assessment):
        _materialize_task_repository(sandbox, task)
    elif not cloned:
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")

    bootstrap_result = _run_workspace_bootstrap(e2b, sandbox, task, repo_root)
    if bootstrap_result.get("ran"):
        append_assessment_timeline_event(
            assessment,
            "workspace_bootstrap",
            {
                "success": bool(bootstrap_result.get("success")),
                "must_succeed": bool(bootstrap_result.get("must_succeed")),
                "working_dir": bootstrap_result.get("working_dir"),
                "steps": bootstrap_result.get("steps") or [],
            },
        )
        if not bootstrap_result.get("success") and bootstrap_result.get("must_succeed"):
            raise HTTPException(
                status_code=500,
                detail="Failed to prepare assessment workspace. Please try again later.",
            )
    return repo_root


def _connect_assessment_sandbox(e2b: object, assessment: Assessment, task: Task, db: Session) -> tuple[object, str]:
    if assessment.e2b_session_id:
        try:
            sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
        except Exception:
            sandbox = e2b.create_sandbox()
            assessment.e2b_session_id = e2b.get_sandbox_id(sandbox)
    else:
        sandbox = e2b.create_sandbox()
        assessment.e2b_session_id = e2b.get_sandbox_id(sandbox)

    repo_root = _ensure_assessment_workspace_ready(e2b, sandbox, assessment, task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist assessment sandbox/session state assessment_id=%s", assessment.id)
    return sandbox, repo_root


def _sync_repo_files_to_sandbox(sandbox: object, repo_root: str, repo_files: dict[str, str]) -> None:
    if not repo_files:
        return
    files_api = getattr(sandbox, "files", None)
    if files_api is None or not hasattr(files_api, "write"):
        raise HTTPException(status_code=500, detail="Sandbox file sync is unavailable")

    for rel_path, content in repo_files.items():
        safe_path = _sanitize_repo_path(rel_path)
        if not safe_path:
            continue
        target_path = f"{repo_root}/{safe_path}"
        sandbox.run_code(
            "import pathlib\n"
            f"pathlib.Path({target_path!r}).parent.mkdir(parents=True, exist_ok=True)\n"
        )
        files_api.write(target_path, str(content or ""))


def _python_module_path(selected_file_path: str | None) -> str | None:
    path = _sanitize_repo_path(selected_file_path)
    if not path:
        return None
    if not path.lower().endswith(".py"):
        return None

    module_path = path[:-3]
    parts = [part for part in module_path.split("/") if part]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    for part in parts:
        if not re.match(_PYTHON_MODULE_PART_RE, part):
            return None
    return ".".join(parts)


def _shell_python_prefix() -> str:
    return (
        'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"; '
        'PYTHON_BIN="./.venv/bin/python"; '
        '[ -x "$PYTHON_BIN" ] || PYTHON_BIN=python; '
    )


def _build_run_command(selected_file_path: str | None, *, task: Task | None = None) -> str | None:
    path = _sanitize_repo_path(selected_file_path)
    if not path:
        return None

    basename = path.rsplit("/", 1)[-1]
    quoted_path = shlex.quote(path)
    lower_path = path.lower()
    shell_prefix = _shell_python_prefix()
    if lower_path.endswith(".py"):
        if path.startswith("tests/") or "/tests/" in path or basename.startswith("test_"):
            test_runner = str((_task_extra_data(task).get("test_runner") or {}).get("command") or "").strip() if task else ""
            if test_runner:
                return f'{shell_prefix}{test_runner} {quoted_path}'
            return f'{shell_prefix}"$PYTHON_BIN" -m pytest -q {quoted_path}'

        module_path = _python_module_path(path)
        if module_path:
            return f'{shell_prefix}"$PYTHON_BIN" -m {shlex.quote(module_path)}'
        return f'{shell_prefix}"$PYTHON_BIN" {quoted_path}'
    if lower_path.endswith((".sh", ".bash")):
        return f"bash {quoted_path}"
    if lower_path.endswith((".js", ".mjs", ".cjs")):
        return f"node {quoted_path}"
    return None


def _run_selected_repo_file(e2b: object, sandbox: object, task: Task, selected_file_path: str | None) -> dict:
    repo_root = _workspace_repo_root(task)
    command = _build_run_command(selected_file_path, task=task)
    if not command:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": "No default Run action exists for this file type. Use the terminal for repo commands or select a runnable source/test file.",
            "results": [],
            "command": None,
            "working_dir": repo_root,
        }

    try:
        process = e2b.run_command(
            sandbox,
            command,
            cwd=repo_root,
            timeout=60,
        )
        stdout, stderr, exit_code = _extract_process_output(process)
        success = exit_code in (None, 0)
        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "error": None if success else (f"Command exited with code {exit_code}" if exit_code is not None else "Command failed"),
            "results": [],
            "command": command,
            "working_dir": repo_root,
            "exit_code": exit_code,
        }
    except Exception as exc:
        stdout, stderr, exit_code = _extract_process_output(exc)
        return {
            "success": False,
            "stdout": stdout,
            "stderr": stderr,
            "error": str(exc),
            "results": [],
            "command": command,
            "working_dir": repo_root,
            "exit_code": exit_code,
        }


def _ensure_demo_org(db: Session):
    from ...models.organization import Organization

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

    return None


def _upsert_demo_candidate(
    *,
    db: Session,
    org_id: int,
    full_name: str,
    position: str | None,
    email: str,
    work_email: str | None,
    company_name: str,
    company_size: str,
    marketing_consent: bool,
    lead_source: str,
    workable_data_updates: dict[str, object] | None = None,
) -> Candidate:
    normalized_email = str(email).strip().lower()
    normalized_work_email = str(work_email).strip().lower() if work_email else None

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org_id,
            Candidate.email == normalized_email,
        )
        .first()
    )
    if not candidate:
        candidate = Candidate(
            organization_id=org_id,
            email=normalized_email,
        )
        db.add(candidate)
        db.flush()

    existing_workable_data = candidate.workable_data if isinstance(candidate.workable_data, dict) else {}

    candidate.full_name = full_name
    candidate.position = position
    candidate.work_email = normalized_work_email
    candidate.company_name = company_name
    candidate.company_size = company_size
    candidate.lead_source = lead_source
    candidate.marketing_consent = bool(marketing_consent)
    candidate.workable_data = {
        **existing_workable_data,
        **(workable_data_updates or {}),
    }
    return candidate


def _get_feedback_assessment_or_404(token: str, db: Session) -> Assessment:
    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.organization),
        )
        .filter(Assessment.token == token)
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    return assessment


def _feedback_payload_response(assessment: Assessment) -> dict:
    org_enabled = bool(getattr(assessment.organization, "candidate_feedback_enabled", True))
    assessment_enabled = bool(getattr(assessment, "candidate_feedback_enabled", True))
    if not org_enabled or not assessment_enabled:
        raise HTTPException(status_code=404, detail="Feedback is unavailable for this assessment")
    feedback = getattr(assessment, "candidate_feedback_json", None)
    if not bool(getattr(assessment, "candidate_feedback_ready", False)) or not isinstance(feedback, dict):
        raise HTTPException(status_code=403, detail="Your feedback report is not ready yet")
    candidate_name = (
        (assessment.candidate.full_name if assessment.candidate else None)
        or (assessment.candidate.email if assessment.candidate else None)
        or "Candidate"
    )
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "feedback_ready": True,
        "feedback_generated_at": getattr(assessment, "candidate_feedback_generated_at", None),
        "feedback_sent_at": getattr(assessment, "candidate_feedback_sent_at", None),
        "organization_name": assessment.organization.name if assessment.organization else None,
        "task_name": assessment.task.name if assessment.task else None,
        "role_name": assessment.role.name if assessment.role else None,
        "candidate_name": candidate_name,
        "feedback": feedback,
    }


@router.post("/token/{token}/start", response_model=AssessmentStart)
def start_assessment(
    token: str,
    payload: AssessmentStartRequest | None = None,
    db: Session = Depends(get_db),
):
    """Candidate starts or resumes an assessment via token."""
    assessment = db.query(Assessment).filter(Assessment.token == token).with_for_update().first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    try:
        return start_or_resume_assessment(
            assessment,
            db,
            calibration_warmup_prompt=(payload.calibration_warmup_prompt if payload else None),
        )
    except HTTPException as exc:
        if exc.status_code == 402:
            raise HTTPException(status_code=402, detail=CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE) from exc
        raise


@router.get("/token/{token}/preview")
def preview_assessment(token: str, db: Session = Depends(get_db)):
    """Return candidate-facing task context without starting the assessment timer."""
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    extra_data = task.extra_data if isinstance(task.extra_data, dict) else {}
    task_calibration_prompt = (
        (task.calibration_prompt or "").strip()
        or str(extra_data.get("calibration_prompt") or "").strip()
        or (settings.DEFAULT_CALIBRATION_PROMPT or "").strip()
    )
    start_gate = get_assessment_start_gate(assessment, db)
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "candidate_name": getattr(getattr(assessment, "candidate", None), "full_name", None),
        "organization_name": getattr(getattr(assessment, "organization", None), "name", None),
        "status": str(getattr(assessment.status, "value", assessment.status) or ""),
        "expires_at": assessment.expires_at,
        "invite_sent_at": getattr(assessment, "invite_sent_at", None),
        "duration_minutes": assessment.duration_minutes,
        "start_gate": {
            "can_start": bool(start_gate.get("can_start")),
            "reason": start_gate.get("reason"),
            "message": start_gate.get("message"),
        },
        "task": {
            "name": task.name,
            "role": task.role,
            "description": task.description,
            "scenario": task.scenario,
            "duration_minutes": assessment.duration_minutes,
            "repo_structure": _preview_repo_structure(task.repo_structure),
            "rubric_categories": candidate_rubric_view(task.evaluation_rubric),
            "expected_candidate_journey": extra_data.get("expected_candidate_journey"),
            "calibration_enabled": not settings.MVP_DISABLE_CALIBRATION,
            "calibration_prompt": task_calibration_prompt if not settings.MVP_DISABLE_CALIBRATION else None,
            "has_cv_on_file": bool(
                assessment.cv_filename
                or (assessment.candidate.cv_filename if getattr(assessment, "candidate", None) else None)
            ),
        },
        "ai_mode": getattr(assessment, "ai_mode", "claude_cli_terminal"),
        "terminal_mode": getattr(assessment, "ai_mode", "claude_cli_terminal") == "claude_cli_terminal",
        "terminal_capabilities": terminal_capabilities(),
        "clone_command": getattr(assessment, "clone_command", None),
    }


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

    candidate = _upsert_demo_candidate(
        db=db,
        org_id=org.id,
        full_name=data.full_name,
        position=data.position,
        email=data.email,
        work_email=data.work_email,
        company_name=data.company_name,
        company_size=data.company_size,
        marketing_consent=bool(data.marketing_consent),
        lead_source="landing_demo",
        workable_data_updates={
            "demo_track": track,
            "marketing_consent": bool(data.marketing_consent),
        },
    )

    normalized_email = str(data.email).strip().lower()
    normalized_work_email = str(data.work_email).strip().lower() if data.work_email else None

    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=secrets.token_urlsafe(32),
        duration_minutes=task.duration_minutes or 30,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        is_demo=True,
        demo_track=track,
        candidate_feedback_enabled=bool(getattr(org, "candidate_feedback_enabled", True)),
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


@router.post("/demo/request", response_model=DemoBookingResponse)
def request_demo_walkthrough(
    data: DemoBookingRequest,
    db: Session = Depends(get_db),
):
    """Store a public demo-booking lead without starting a candidate runtime session."""
    org = _ensure_demo_org(db)
    candidate = _upsert_demo_candidate(
        db=db,
        org_id=org.id,
        full_name=data.full_name,
        position=data.position,
        email=data.email,
        work_email=data.work_email,
        company_name=data.company_name,
        company_size=data.company_size,
        marketing_consent=bool(data.marketing_consent),
        lead_source="book_demo",
        workable_data_updates={
            "demo_request": {
                "requested_at": utcnow().isoformat(),
                "source": "book_demo_page",
                "marketing_consent": bool(data.marketing_consent),
            },
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save demo request")

    db.refresh(candidate)
    return DemoBookingResponse(candidate_id=candidate.id)


@router.get("/{token}/feedback")
def get_candidate_feedback(
    token: str,
    db: Session = Depends(get_db),
):
    assessment = _get_feedback_assessment_or_404(token, db)
    return _feedback_payload_response(assessment)


@router.get("/{token}/feedback.pdf")
def download_candidate_feedback_pdf(
    token: str,
    db: Session = Depends(get_db),
):
    assessment = _get_feedback_assessment_or_404(token, db)
    payload = _feedback_payload_response(assessment)
    report_text = build_feedback_text_report(payload.get("feedback") or {})
    pdf = build_plain_text_pdf(report_text)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="candidate-feedback-{assessment.id}.pdf"'},
    )


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
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
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
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    e2b = build_sandbox_adapter()
    sandbox, repo_root = _connect_assessment_sandbox(e2b, assessment, task, db)
    repo_files = _normalize_runtime_repo_files(data.repo_files)
    if repo_files:
        _sync_repo_files_to_sandbox(sandbox, repo_root, repo_files)
    t0 = time.time()
    if _sanitize_repo_path(data.selected_file_path):
        result = _run_selected_repo_file(e2b, sandbox, task, data.selected_file_path)
    else:
        result = e2b.execute_code(sandbox, data.code)
        if isinstance(result, dict):
            result.setdefault("command", None)
            result.setdefault("working_dir", repo_root)
    exec_latency_ms = int((time.time() - t0) * 1000)

    append_assessment_timeline_event(
        assessment,
        "code_execute",
        {
            "session_id": assessment.e2b_session_id,
            "code_length": len(data.code or ""),
            "latency_ms": exec_latency_ms,
            "has_stderr": bool(result.get("stderr")),
            "tests_passed": result.get("tests_passed"),
            "tests_total": result.get("tests_total"),
            "selected_file_path": _sanitize_repo_path(data.selected_file_path),
            "command": result.get("command"),
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
    return result


@router.post("/{assessment_id}/repo-file")
def save_repo_file(
    assessment_id: int,
    data: RepoFileSaveRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    safe_path = _sanitize_repo_path(data.path)
    if not safe_path:
        raise HTTPException(status_code=400, detail="Invalid repository file path")

    e2b = build_sandbox_adapter()
    sandbox, repo_root = _connect_assessment_sandbox(e2b, assessment, task, db)
    _sync_repo_files_to_sandbox(
        sandbox,
        repo_root,
        {safe_path: str(data.content or "")},
    )

    append_assessment_timeline_event(
        assessment,
        "repo_file_save",
        {
            "session_id": assessment.e2b_session_id,
            "path": safe_path,
            "content_length": len(str(data.content or "")),
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()

    return {
        "success": True,
        "path": safe_path,
        "message": f"Saved {safe_path}",
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
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    e2b = build_sandbox_adapter()
    sandbox, repo_root = _connect_assessment_sandbox(e2b, assessment, task, db)
    repo_files = _normalize_runtime_repo_files(data.repo_files)
    if repo_files:
        _sync_repo_files_to_sandbox(sandbox, repo_root, repo_files)
    return _submit_assessment(assessment, data.final_code, data.tab_switch_count, db)
