"""Assessment API routes — thin handlers that delegate to the service layer."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import secrets
import time

from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.config import settings
from ...models.user import User
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.task import Task
from ...models.organization import Organization
from ...schemas.assessment import (
    AssessmentCreate, AssessmentResponse, AssessmentStart,
    CodeExecutionRequest, ClaudeRequest, SubmitRequest,
)
from ...services.e2b_service import E2BService
from ...services.claude_service import ClaudeService
from ...components.notifications.service import send_assessment_invite_sync, send_results_notification_sync
from ...services.ai_assisted_evaluator import generate_ai_suggestions
from ...components.assessments.repository import (
    utcnow, ensure_utc, assessment_to_response, build_timeline,
    get_active_assessment, validate_assessment_token, append_assessment_timeline_event,
)
from ...components.assessments.service import (
    store_cv_upload, start_or_resume_assessment, submit_assessment as _submit_assessment, enforce_active_or_timeout,
)

router = APIRouter(prefix="/assessments", tags=["Assessments"])


@router.post("/", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
def create_assessment(
    data: AssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new assessment and send invite email to candidate."""
    if data.duration_minutes < 15 or data.duration_minutes > 180:
        raise HTTPException(status_code=400, detail="duration_minutes must be between 15 and 180")

    try:
        candidate = db.query(Candidate).filter(
            Candidate.email == data.candidate_email,
            Candidate.organization_id == current_user.organization_id,
        ).first()
        if not candidate:
            candidate = Candidate(
                email=data.candidate_email,
                full_name=data.candidate_name or None,
                organization_id=current_user.organization_id,
            )
            db.add(candidate)
            db.flush()
        elif data.candidate_name:
            candidate.full_name = data.candidate_name

        task = db.query(Task).filter(
            Task.id == data.task_id,
            (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        token = secrets.token_urlsafe(32)
        assessment = Assessment(
            organization_id=current_user.organization_id,
            candidate_id=candidate.id,
            task_id=data.task_id,
            token=token,
            duration_minutes=data.duration_minutes,
            expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        import logging as _logging
        _logging.getLogger("tali.assessments").exception("Failed to create assessment")
        raise HTTPException(status_code=500, detail="Failed to create assessment")

    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(Assessment.id == assessment.id)
        .first()
    )

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "Your recruiter"
    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=data.candidate_email,
            candidate_name=data.candidate_name or data.candidate_email,
            token=token,
            assessment_id=assessment.id,
            org_name=org_name,
            position=task.name or "Technical assessment",
        )
    else:
        from ...tasks.assessment_tasks import send_assessment_email
        send_assessment_email.delay(
            candidate_email=data.candidate_email,
            candidate_name=data.candidate_name or data.candidate_email,
            token=token,
            org_name=org_name,
            position=task.name or "Technical assessment",
            assessment_id=assessment.id,
            request_id=get_request_id(),
        )
    return assessment_to_response(assessment, db)


@router.get("/")
def list_assessments(
    status: Optional[str] = None,
    task_id: Optional[int] = None,
    candidate_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List assessments for the current user's organization."""
    q = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(Assessment.organization_id == current_user.organization_id)
    )
    if status:
        q = q.filter(Assessment.status == status)
    if task_id is not None:
        q = q.filter(Assessment.task_id == task_id)
    if candidate_id is not None:
        q = q.filter(Assessment.candidate_id == candidate_id)
    q = q.order_by(Assessment.created_at.desc())
    total = q.count()
    assessments = q.offset(offset).limit(limit).all()
    return {
        "items": [assessment_to_response(a, db) for a in assessments],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single assessment by ID."""
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment_to_response(assessment, db)


@router.post("/token/{token}/start", response_model=AssessmentStart)
def start_assessment(token: str, db: Session = Depends(get_db)):
    """Candidate starts or resumes an assessment via token."""
    assessment = db.query(Assessment).filter(Assessment.token == token).with_for_update().first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
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

    e2b = E2BService(settings.E2B_API_KEY)
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

    claude = ClaudeService(settings.ANTHROPIC_API_KEY)
    messages = data.conversation_history + [{"role": "user", "content": data.message}]

    t0 = time.time()
    response = claude.chat(messages)
    latency_ms = int((time.time() - t0) * 1000)

    prompt_record = {
        "message": data.message,
        "response": response.get("content", ""),
        "timestamp": utcnow().isoformat(),
        "input_tokens": response.get("input_tokens", 0),
        "output_tokens": response.get("output_tokens", 0),
        "tokens_used": response.get("tokens_used", 0),
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
        },
    )

    if len(prompts) == 1 and assessment.started_at:
        started = ensure_utc(assessment.started_at)
        assessment.time_to_first_prompt_seconds = int((utcnow() - started).total_seconds())

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to persist AI interaction")

    return response


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
    return _submit_assessment(assessment, data.final_code, data.tab_switch_count, db)


@router.delete("/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    try:
        db.delete(assessment)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete assessment")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{assessment_id}/resend")
def resend_assessment_invite(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not assessment.candidate:
        raise HTTPException(status_code=400, detail="Assessment has no candidate")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "Your recruiter"
    if settings.MVP_DISABLE_CELERY:
        send_assessment_invite_sync(
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name or assessment.candidate.email,
            token=assessment.token,
            assessment_id=assessment.id,
            org_name=org_name,
            position=(assessment.task.name if assessment.task else "Technical assessment"),
        )
    else:
        from ...tasks.assessment_tasks import send_assessment_email
        send_assessment_email.delay(
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name or assessment.candidate.email,
            token=assessment.token,
            org_name=org_name,
            position=(assessment.task.name if assessment.task else "Technical assessment"),
            assessment_id=assessment.id,
            request_id=get_request_id(),
        )
    return {"success": True}


@router.post("/{assessment_id}/post-to-workable")
def post_assessment_to_workable(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    from ...services.workable_service import WorkableService
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.posted_to_workable:
        return {
            "success": True,
            "already_posted": True,
            "posted_to_workable": True,
            "posted_to_workable_at": assessment.posted_to_workable_at,
        }

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        raise HTTPException(status_code=400, detail="Workable is not connected")
    if not assessment.workable_candidate_id:
        raise HTTPException(status_code=400, detail="Assessment is not linked to a Workable candidate")

    svc = WorkableService(access_token=org.workable_access_token, subdomain=org.workable_subdomain)
    result = svc.post_assessment_result(
        candidate_id=assessment.workable_candidate_id,
        assessment_data={
            "score": assessment.score or 0,
            "tests_passed": assessment.tests_passed or 0,
            "tests_total": assessment.tests_total or 0,
            "time_taken": assessment.duration_minutes,
            "results_url": f"{settings.FRONTEND_URL}/#/dashboard",
        },
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail="Failed to post to Workable")

    assessment.posted_to_workable = True
    assessment.posted_to_workable_at = utcnow()
    db.commit()
    return {
        "success": True,
        "posted_to_workable": True,
        "posted_to_workable_at": assessment.posted_to_workable_at,
    }


@router.get("/{assessment_id}/report.pdf")
def download_assessment_report_pdf(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a branded PDF report without external dependencies."""
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    candidate_name = (assessment.candidate.full_name if assessment.candidate else None) or (
        assessment.candidate.email if assessment.candidate else "Candidate"
    )
    task_name = assessment.task.name if assessment.task else "Assessment"
    status_value = assessment.status.value if hasattr(assessment.status, "value") else str(assessment.status)
    analytics = assessment.prompt_analytics or {}
    components = analytics.get("component_scores", {}) if isinstance(analytics, dict) else {}
    heuristics = analytics.get("heuristics", {}) if isinstance(analytics, dict) else {}
    fraud_flags = assessment.prompt_fraud_flags or []

    component_lines = []
    for key in (
        "tests", "code_quality", "prompt_quality", "prompt_efficiency",
        "independence", "context_utilization", "design_thinking",
        "debugging_strategy", "written_communication",
    ):
        if key in components:
            component_lines.append(f"  - {key}: {components[key]}")
    if not component_lines:
        component_lines.append("  - No component scores available")

    h_focus = (heuristics.get("browser_focus_ratio") or {}).get("ratio")
    h_tab = (heuristics.get("tab_switch_count") or {}).get("count")
    h_first = (heuristics.get("time_to_first_prompt") or {}).get("value")
    analytics_lines = [
        f"  - Browser focus ratio: {h_focus if h_focus is not None else 'N/A'}",
        f"  - Tab switches: {h_tab if h_tab is not None else (assessment.tab_switch_count or 0)}",
        f"  - Time to first prompt (s): {h_first if h_first is not None else 'N/A'}",
    ]

    fraud_lines = [
        f"  - {f.get('type')}: {f.get('evidence', '')} (confidence={f.get('confidence')})"
        for f in fraud_flags
    ]
    if not fraud_lines:
        fraud_lines = ["  - None detected"]

    body_text = (
        "TALI - AI-Augmented Technical Assessment Report\\n"
        "============================================\\n"
        f"Assessment ID: {assessment.id}\\n"
        f"Candidate: {candidate_name}\\n"
        f"Task: {task_name}\\n"
        f"Status: {status_value}\\n"
        f"Overall Score: {assessment.score if assessment.score is not None else 'N/A'}/10\\n"
        f"Calibration Score: {assessment.calibration_score if assessment.calibration_score is not None else 'N/A'}/10\\n"
        f"Tests Passed: {assessment.tests_passed or 0}/{assessment.tests_total or 0}\\n"
        f"Code Quality: {assessment.code_quality_score if assessment.code_quality_score is not None else 'N/A'}\\n"
        "\\n"
        "Component Score Breakdown\\n"
        "-------------------------\\n"
        f"{chr(10).join(component_lines)}\\n"
        "\\n"
        "Prompt Analytics Summary\\n"
        "------------------------\\n"
        f"{chr(10).join(analytics_lines)}\\n"
        "\\n"
        "Fraud / Proctoring Flags\\n"
        "------------------------\\n"
        f"{chr(10).join(fraud_lines)}\\n"
    )
    escaped = body_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 780 Td ({escaped.replace(chr(10), ') Tj T* (')}) Tj ET"
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    content = stream.encode("latin-1", errors="ignore")
    pdf += f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii") + content + b"\nendstream endobj\n"
    xref_pos = len(pdf)
    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000241 00000 n \n"
        b"0000000311 00000 n \n"
    )
    trailer = f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    final_pdf = pdf + xref + trailer
    return Response(
        content=final_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="assessment-{assessment.id}.pdf"'},
    )


@router.patch("/{assessment_id}/manual-evaluation")
def update_manual_evaluation(
    assessment_id: int,
    body: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save manual rubric evaluation (excellent/good/poor per category + evidence)."""
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    category_scores = body.get("category_scores") or {}
    if not isinstance(category_scores, dict):
        raise HTTPException(status_code=400, detail="category_scores must be an object")
    allowed = {"excellent", "good", "poor"}
    for cat, data in category_scores.items():
        if isinstance(data, dict):
            score = (data.get("score") or "").strip().lower()
            if score and score not in allowed:
                raise HTTPException(status_code=400, detail=f"Score for {cat} must be one of excellent, good, poor")
    from ...services.evaluation_service import calculate_weighted_rubric_score
    rubric = (assessment.task.evaluation_rubric if assessment.task else None) or {}
    overall = None
    if rubric and category_scores:
        scores_flat = {
            k: (v.get("score") if isinstance(v, dict) else v)
            for k, v in category_scores.items()
            if isinstance(v, dict) and v.get("score")
        }
        if scores_flat:
            overall = round(calculate_weighted_rubric_score(scores_flat, rubric) * (10.0 / 3.0), 2)
    assessment.manual_evaluation = {
        "category_scores": category_scores,
        "overall_score": overall,
        "strengths": body.get("strengths") or [],
        "improvements": body.get("improvements") or [],
    }
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save manual evaluation")
    return {"success": True, "manual_evaluation": assessment.manual_evaluation}


@router.post("/{assessment_id}/notes")
def add_assessment_note(
    assessment_id: int,
    body: Dict[str, str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="note is required")
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    existing_timeline = assessment.timeline or []
    existing_timeline.append({
        "time": utcnow().isoformat(),
        "event": "Recruiter note",
        "prompt": note,
    })
    assessment.timeline = existing_timeline
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save note")
    return {"success": True, "timeline": assessment.timeline}


@router.post("/{assessment_id}/ai-eval-suggestions")
def ai_eval_suggestions(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """V2 scaffold: AI suggests rubric scores/evidence; human reviewer decides final scores."""
    if not settings.AI_ASSISTED_EVAL_ENABLED:
        raise HTTPException(status_code=404, detail="AI-assisted evaluation is disabled")

    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    payload = {
        "evaluation_rubric": (assessment.task.evaluation_rubric if assessment.task else {}) or {},
        "chat_log": assessment.ai_prompts or [],
        "git_evidence": getattr(assessment, "git_evidence", {}) or {},
        "test_results": assessment.test_results or {},
    }
    return generate_ai_suggestions(payload)
