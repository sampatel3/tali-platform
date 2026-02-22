from __future__ import annotations

import secrets
import time
from datetime import timedelta

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
    enforce_active_or_timeout,
    enforce_not_paused,
    start_or_resume_assessment,
    store_cv_upload,
    submit_assessment as _submit_assessment,
)
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
from ...services.task_spec_loader import candidate_rubric_view
from ...schemas.assessment import (
    AssessmentStart,
    AssessmentStartRequest,
    CodeExecutionRequest,
    DemoAssessmentStartRequest,
    SubmitRequest,
)

from .candidate_claude_routes import router as candidate_claude_router
from .candidate_terminal_routes import router as candidate_terminal_router

router = APIRouter()
router.include_router(candidate_claude_router)
router.include_router(candidate_terminal_router)


DEMO_ORG_SLUG = "taali-demo"
DEMO_ORG_NAME = "TAALI Demo Leads"
DEMO_TRACK_TASK_KEYS = {
    # Primary demo track: canonical tasks for product demos (from tasks/*.json).
    "data_eng_super_platform_crisis": "data_eng_super_platform_crisis",
    "ai_eng_super_production_launch": "ai_eng_super_production_launch",
    # Backward-compatible aliases (route to current tasks; legacy keys removed from repo).
    "data_eng_a_pipeline_reliability": "data_eng_super_platform_crisis",
    "data_eng_b_cdc_fix": "data_eng_super_platform_crisis",
    "data_eng_c_backfill_schema": "data_eng_super_platform_crisis",
    "backend-reliability": "data_eng_super_platform_crisis",
    "frontend-debugging": "data_eng_super_platform_crisis",
    "data-pipeline": "data_eng_super_platform_crisis",
}
DEMO_TRACK_KEYS = set(DEMO_TRACK_TASK_KEYS.keys())


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
    return start_or_resume_assessment(
        assessment,
        db,
        calibration_warmup_prompt=(payload.calibration_warmup_prompt if payload else None),
    )


@router.get("/token/{token}/preview")
def preview_assessment(token: str, db: Session = Depends(get_db)):
    """Return candidate-facing task context without starting the assessment timer."""
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
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
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "status": str(getattr(assessment.status, "value", assessment.status) or ""),
        "expires_at": assessment.expires_at,
        "duration_minutes": assessment.duration_minutes,
        "task": {
            "name": task.name,
            "role": task.role,
            "description": task.description,
            "scenario": task.scenario,
            "duration_minutes": assessment.duration_minutes,
            "rubric_categories": candidate_rubric_view(task.evaluation_rubric),
            "expected_candidate_journey": extra_data.get("expected_candidate_journey"),
            "calibration_enabled": not settings.MVP_DISABLE_CALIBRATION,
            "calibration_prompt": task_calibration_prompt if not settings.MVP_DISABLE_CALIBRATION else None,
            "has_cv_on_file": bool(
                assessment.cv_filename
                or (assessment.candidate.cv_filename if getattr(assessment, "candidate", None) else None)
            ),
        },
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
            "tests_passed": result.get("tests_passed"),
            "tests_total": result.get("tests_total"),
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
    return result


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
