from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import secrets
import json

from ...core.database import get_db
from ...core.security import get_current_user
from ...core.config import settings
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

router = APIRouter(prefix="/assessments", tags=["Assessments"])


def _ensure_utc(dt: datetime) -> datetime:
    """Return datetime as timezone-aware UTC for subtraction."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_timeline(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build timeline events for candidate detail (start, optional AI prompts, submit)."""
    events = []
    if assessment.started_at:
        events.append({"time": "00:00", "event": "Started assessment"})
    prompts = assessment.ai_prompts or []
    start_utc = _ensure_utc(assessment.started_at) if assessment.started_at else None
    for p in prompts:
        ts = p.get("timestamp") or ""
        if ts and start_utc:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta_sec = int((dt - start_utc).total_seconds())
                mm, ss = delta_sec // 60, delta_sec % 60
                time_str = f"{mm:02d}:{ss:02d}"
            except Exception:
                time_str = "—"
        else:
            time_str = "—"
        events.append({
            "time": time_str,
            "event": "Used AI assistant",
            "prompt": p.get("message", ""),
        })
    if assessment.completed_at and assessment.started_at:
        end_utc = _ensure_utc(assessment.completed_at)
        if start_utc:
            delta_sec = int((end_utc - start_utc).total_seconds())
            mm, ss = delta_sec // 60, delta_sec % 60
            time_str = f"{mm:02d}:{ss:02d}"
        else:
            time_str = "—"
        events.append({"time": time_str, "event": "Submitted assessment"})
    elif assessment.completed_at:
        events.append({"time": "—", "event": "Submitted assessment"})
    return events if events else (assessment.timeline or [])


def _build_prompts_list(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build prompts_list for candidate detail from ai_prompts (no per-prompt assessment yet)."""
    prompts = assessment.ai_prompts or []
    return [
        {"text": p.get("message", ""), "assessment": p.get("assessment", "")}
        for p in prompts
    ]


def _build_results(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build results list for candidate detail from test_results and code quality."""
    results = []
    if assessment.tests_total is not None and assessment.tests_total > 0:
        results.append({
            "title": "Test suite",
            "score": f"{assessment.tests_passed or 0}/{assessment.tests_total}",
            "description": f"Passed {assessment.tests_passed or 0} of {assessment.tests_total} tests.",
        })
    if assessment.test_results and isinstance(assessment.test_results, dict):
        err = assessment.test_results.get("error")
        if err:
            results.append({
                "title": "Execution",
                "score": "—",
                "description": err,
            })
    # Code quality summary if we have it in test_results or code_snapshots
    if assessment.code_quality_score is not None:
        results.append({
            "title": "Code quality",
            "score": f"{assessment.code_quality_score}/10",
            "description": "Claude code quality analysis applied.",
        })
    return results


def _build_breakdown(assessment: Assessment) -> Dict[str, Any]:
    """Build breakdown for candidate detail (tests, code quality, AI usage)."""
    breakdown = {}
    if assessment.tests_passed is not None and assessment.tests_total is not None:
        breakdown["testsPassed"] = f"{assessment.tests_passed}/{assessment.tests_total}"
    if assessment.code_quality_score is not None:
        breakdown["codeQuality"] = assessment.code_quality_score
    if assessment.ai_usage_score is not None:
        breakdown["aiUsage"] = assessment.ai_usage_score
    elif assessment.ai_prompts:
        # Simple heuristic: 1–5 prompts = 8, 6–10 = 7, 11+ = 6
        n = len(assessment.ai_prompts)
        breakdown["aiUsage"] = 8 if n <= 5 else (7 if n <= 10 else 6)
    if assessment.time_efficiency_score is not None:
        breakdown["timeEfficiency"] = assessment.time_efficiency_score
    breakdown["bugsFixed"] = breakdown.get("testsPassed", "—")
    return breakdown


def _assessment_to_response(assessment: Assessment) -> Dict[str, Any]:
    """Serialize assessment to response dict with computed prompts_list, results, breakdown, timeline, and candidate/task names."""
    candidate_name = ""
    candidate_email = ""
    if assessment.candidate:
        candidate_name = assessment.candidate.full_name or assessment.candidate.email or ""
        candidate_email = assessment.candidate.email or ""
    task_name = assessment.task.name if assessment.task else ""
    data = {
        "id": assessment.id,
        "organization_id": assessment.organization_id,
        "candidate_id": assessment.candidate_id,
        "task_id": assessment.task_id,
        "token": assessment.token,
        "status": assessment.status.value if hasattr(assessment.status, "value") else str(assessment.status),
        "duration_minutes": assessment.duration_minutes,
        "started_at": assessment.started_at,
        "completed_at": assessment.completed_at,
        "expires_at": assessment.expires_at,
        "score": assessment.score,
        "tests_passed": assessment.tests_passed,
        "tests_total": assessment.tests_total,
        "code_quality_score": assessment.code_quality_score,
        "time_efficiency_score": assessment.time_efficiency_score,
        "ai_usage_score": assessment.ai_usage_score,
        "test_results": assessment.test_results,
        "ai_prompts": assessment.ai_prompts,
        "timeline": assessment.timeline or _build_timeline(assessment),
        "created_at": assessment.created_at,
        "prompts_list": _build_prompts_list(assessment),
        "results": _build_results(assessment),
        "breakdown": _build_breakdown(assessment),
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "task_name": task_name,
    }
    return data


def _get_active_assessment(assessment_id: int, db: Session) -> Assessment:
    """Get an in-progress assessment, raising 404 if not found or not active."""
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.status == AssessmentStatus.IN_PROGRESS,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Active assessment not found")
    return assessment


def _validate_assessment_token(assessment: Assessment, token: str) -> None:
    """Verify the provided token matches the assessment's token."""
    if not secrets.compare_digest(assessment.token, token):
        raise HTTPException(status_code=403, detail="Invalid assessment token")


@router.post("/", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
def create_assessment(
    data: AssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new assessment and send invite email to candidate."""
    # Create or get candidate; keep name in sync when provided
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

    # Verify task exists and belongs to the user's organization
    task = db.query(Task).filter(
        Task.id == data.task_id,
        Task.organization_id == current_user.organization_id,
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
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "Your recruiter"
    from ...tasks.assessment_tasks import send_assessment_email
    send_assessment_email.delay(
        candidate_email=data.candidate_email,
        candidate_name=data.candidate_name or data.candidate_email,
        token=token,
        org_name=org_name,
        position=task.name or "Technical assessment",
    )
    return _assessment_to_response(assessment)


@router.get("/")
def list_assessments(
    status: Optional[str] = None,
    task_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List assessments for the current user's organization with optional filters and pagination."""
    q = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(Assessment.organization_id == current_user.organization_id)
    )
    if status:
        q = q.filter(Assessment.status == status)
    if task_id is not None:
        q = q.filter(Assessment.task_id == task_id)
    q = q.order_by(Assessment.created_at.desc())
    total = q.count()
    assessments = q.offset(offset).limit(limit).all()
    return {
        "items": [_assessment_to_response(a) for a in assessments],
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
    return _assessment_to_response(assessment)


@router.post("/token/{token}/start", response_model=AssessmentStart)
def start_assessment(token: str, db: Session = Depends(get_db)):
    """Candidate starts an assessment via their unique token. No auth required."""
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if assessment.status != AssessmentStatus.PENDING:
        raise HTTPException(status_code=400, detail="Assessment already started or completed")
    if assessment.expires_at and assessment.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")

    # Create E2B sandbox
    e2b = E2BService(settings.E2B_API_KEY)
    sandbox = e2b.create_sandbox()

    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.utcnow()
    assessment.e2b_session_id = sandbox.id
    db.commit()

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "sandbox_id": sandbox.id,
        "task": {
            "name": task.name,
            "description": task.description,
            "starter_code": task.starter_code,
            "duration_minutes": assessment.duration_minutes,
        },
        "time_remaining": assessment.duration_minutes * 60,
    }


@router.post("/{assessment_id}/execute")
def execute_code(
    assessment_id: int,
    data: CodeExecutionRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Execute code in the assessment's E2B sandbox (reuses sandbox from start)."""
    assessment = _get_active_assessment(assessment_id, db)
    _validate_assessment_token(assessment, x_assessment_token)

    e2b = E2BService(settings.E2B_API_KEY)
    if assessment.e2b_session_id:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
    else:
        sandbox = e2b.create_sandbox()
        assessment.e2b_session_id = sandbox.id
        db.commit()
    result = e2b.execute_code(sandbox, data.code)
    return result


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Send a message to Claude AI assistant during assessment."""
    assessment = _get_active_assessment(assessment_id, db)
    _validate_assessment_token(assessment, x_assessment_token)

    claude = ClaudeService(settings.ANTHROPIC_API_KEY)
    messages = data.conversation_history + [{"role": "user", "content": data.message}]
    response = claude.chat(messages)

    # Track AI usage
    if assessment.ai_prompts is None:
        assessment.ai_prompts = []
    assessment.ai_prompts = assessment.ai_prompts + [{
        "message": data.message,
        "timestamp": datetime.utcnow().isoformat(),
    }]
    db.commit()

    return response


@router.post("/{assessment_id}/submit")
def submit_assessment(
    assessment_id: int,
    data: SubmitRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Submit the assessment, run tests, and calculate score."""
    assessment = _get_active_assessment(assessment_id, db)
    _validate_assessment_token(assessment, x_assessment_token)

    task = db.query(Task).filter(Task.id == assessment.task_id).first()

    # Run tests in the same sandbox the candidate used (or create one if missing)
    e2b = E2BService(settings.E2B_API_KEY)
    if assessment.e2b_session_id:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
    else:
        sandbox = e2b.create_sandbox()

    sandbox.files.write("/tmp/solution.py", data.final_code)
    test_results = e2b.run_tests(sandbox, task.test_code) if task.test_code else {"passed": 0, "failed": 0, "total": 0}
    e2b.close_sandbox(sandbox)

    # Calculate score
    passed = test_results.get("passed", 0)
    total = test_results.get("total", 0)
    tests_score = (passed / total * 10) if total > 0 else 0

    # Code quality analysis
    claude = ClaudeService(settings.ANTHROPIC_API_KEY)
    quality = claude.analyze_code_quality(data.final_code)
    code_quality_score = None
    if quality.get("success") and quality.get("analysis"):
        try:
            analysis = json.loads(quality["analysis"])
            code_quality_score = analysis.get("overall_score")
            if code_quality_score is not None:
                code_quality_score = float(code_quality_score)
        except (json.JSONDecodeError, TypeError):
            pass

    assessment.status = AssessmentStatus.COMPLETED
    assessment.completed_at = datetime.now(timezone.utc)
    assessment.score = round(tests_score, 1)
    assessment.tests_passed = passed
    assessment.tests_total = total
    assessment.test_results = test_results
    assessment.code_snapshots = [{"final": data.final_code}]
    assessment.timeline = _build_timeline(assessment)
    if code_quality_score is not None:
        assessment.code_quality_score = code_quality_score
    if assessment.ai_prompts:
        n = len(assessment.ai_prompts)
        assessment.ai_usage_score = 8.0 if n <= 5 else (7.0 if n <= 10 else 6.0)
    db.commit()
    db.refresh(assessment)

    # Notify hiring manager (first user in org) that assessment is complete
    notify_user = db.query(User).filter(User.organization_id == assessment.organization_id).first()
    if notify_user:
        from ...tasks.assessment_tasks import send_results_email
        candidate_name = (assessment.candidate.full_name or assessment.candidate.email) if assessment.candidate else "Candidate"
        send_results_email.delay(
            user_email=notify_user.email,
            candidate_name=candidate_name,
            score=assessment.score,
            assessment_id=assessment.id,
        )

    return {
        "success": True,
        "score": assessment.score,
        "tests_passed": passed,
        "tests_total": total,
        "quality_analysis": quality.get("analysis") if quality.get("success") else None,
    }
