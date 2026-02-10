from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta
import secrets

from ...core.database import get_db
from ...core.security import get_current_user
from ...core.config import settings
from ...models.user import User
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.task import Task
from ...schemas.assessment import (
    AssessmentCreate, AssessmentResponse, AssessmentStart,
    CodeExecutionRequest, ClaudeRequest, SubmitRequest,
)
from ...services.e2b_service import E2BService
from ...services.claude_service import ClaudeService

router = APIRouter(prefix="/assessments", tags=["Assessments"])


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
    # Create or get candidate
    candidate = db.query(Candidate).filter(
        Candidate.email == data.candidate_email,
        Candidate.organization_id == current_user.organization_id,
    ).first()
    if not candidate:
        candidate = Candidate(
            email=data.candidate_email,
            full_name=data.candidate_name,
            organization_id=current_user.organization_id,
        )
        db.add(candidate)
        db.flush()

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

    # TODO: send invite email via Celery task
    return assessment


@router.get("/", response_model=List[AssessmentResponse])
def list_assessments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all assessments for the current user's organization."""
    return (
        db.query(Assessment)
        .filter(Assessment.organization_id == current_user.organization_id)
        .order_by(Assessment.created_at.desc())
        .all()
    )


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single assessment by ID."""
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


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
    """Execute code in the assessment's E2B sandbox."""
    assessment = _get_active_assessment(assessment_id, db)
    _validate_assessment_token(assessment, x_assessment_token)

    e2b = E2BService(settings.E2B_API_KEY)
    # NOTE: In production, reconnect to existing sandbox via assessment.e2b_session_id
    sandbox = e2b.create_sandbox()
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

    # Run tests in sandbox
    e2b = E2BService(settings.E2B_API_KEY)
    sandbox = e2b.create_sandbox()

    # Write candidate code and run tests
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

    assessment.status = AssessmentStatus.COMPLETED
    assessment.completed_at = datetime.utcnow()
    assessment.score = round(tests_score, 1)
    assessment.tests_passed = passed
    assessment.tests_total = total
    assessment.test_results = test_results
    assessment.code_snapshots = [{"final": data.final_code}]
    db.commit()
    db.refresh(assessment)

    return {
        "success": True,
        "score": assessment.score,
        "tests_passed": passed,
        "tests_total": total,
        "quality_analysis": quality.get("analysis") if quality.get("success") else None,
    }
