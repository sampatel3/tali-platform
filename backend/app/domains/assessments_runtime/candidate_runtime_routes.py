from __future__ import annotations

import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
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
from ...schemas.assessment import (
    AssessmentStart,
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
    # Primary demo tracks: match the task keys that are live on the platform.
    "data_eng_b_cdc_fix": "data_eng_b_cdc_fix",
    "data_eng_c_backfill_schema": "data_eng_c_backfill_schema",
    # Backward-compatible legacy demo tracks (map to a live task key).
    "backend-reliability": "data_eng_b_cdc_fix",
    "frontend-debugging": "data_eng_b_cdc_fix",
    "data-pipeline": "data_eng_c_backfill_schema",
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
