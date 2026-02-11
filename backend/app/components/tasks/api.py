import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from ...platform.database import get_db
from ...platform.security import get_current_user
from ...platform.config import settings
from ...models.user import User
from ...models.task import Task
from ...schemas.task import TaskCreate, TaskResponse, TaskUpdate
from ...services.claude_service import ClaudeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


# --- Schemas for AI generation ---

class TaskGenerateRequest(BaseModel):
    prompt: str  # e.g. "Create a debugging task for a senior Python engineer focused on async/await"
    difficulty: Optional[str] = None  # optional hint
    duration_minutes: Optional[int] = None  # optional hint


class TaskGenerateResponse(BaseModel):
    """AI-generated task content — NOT yet saved. The user can review/edit before saving."""
    name: str
    description: str
    task_type: str
    difficulty: str
    duration_minutes: int
    starter_code: str
    test_code: str


# --- AI generation endpoint ---

TASK_GEN_SYSTEM = """You are an expert technical assessment designer for software engineering roles.
Given a description of what the employer wants to test, generate a complete coding assessment task.

You MUST respond with ONLY a valid JSON object (no markdown, no ```json blocks) with exactly these keys:
- name: string — short task title (e.g. "Async Pipeline Debugging")
- description: string — 2-4 sentence description of what the candidate must do, written for the candidate
- task_type: string — one of: debugging, ai_engineering, optimization, build, refactor
- difficulty: string — one of: junior, mid, senior, staff
- duration_minutes: integer — recommended time (15-90)
- starter_code: string — complete Python starter code with intentional issues or scaffolding (20-80 lines, include comments explaining what's expected)
- test_code: string — pytest test suite that validates the solution (include 3-6 tests)

Make the starter code realistic and production-quality in style. Include realistic bugs or incomplete sections that test real engineering skills.
The test_code should use pytest conventions and actually test meaningful behavior."""


@router.post("/generate", response_model=TaskGenerateResponse)
def generate_task(
    data: TaskGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """Use Claude to generate a complete task from a natural language prompt."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "skip":
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    claude = ClaudeService(settings.ANTHROPIC_API_KEY)

    user_message = data.prompt
    if data.difficulty:
        user_message += f"\nDifficulty level: {data.difficulty}"
    if data.duration_minutes:
        user_message += f"\nTarget duration: {data.duration_minutes} minutes"

    result = claude.chat(
        messages=[{"role": "user", "content": user_message}],
        system=TASK_GEN_SYSTEM,
    )

    if not result["success"]:
        raise HTTPException(status_code=502, detail="Failed to generate task — Claude API error")

    # Parse the JSON response
    try:
        raw = result["content"].strip()
        # Strip markdown code fences if Claude included them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]  # remove first line
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        generated = json.loads(raw)
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse Claude task generation response: %s\nRaw: %s", e, result["content"][:500])
        raise HTTPException(status_code=502, detail="AI generated an invalid response — please try again")

    # Validate required keys
    required = ["name", "description", "task_type", "difficulty", "duration_minutes", "starter_code", "test_code"]
    missing = [k for k in required if k not in generated]
    if missing:
        raise HTTPException(status_code=502, detail="AI response was incomplete — please try again")

    return TaskGenerateResponse(**{k: generated[k] for k in required})


# --- Standard CRUD ---

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = Task(organization_id=current_user.organization_id, **data.model_dump())
    db.add(task)
    try:
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create task")
    return task


@router.get("/", response_model=List[TaskResponse])
def list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Task).filter(
        (Task.organization_id == current_user.organization_id) | (Task.is_template == True)
    ).all()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.query(Task).filter(
        Task.id == task_id,
        (Task.organization_id == current_user.organization_id) | (Task.is_template == True),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    data: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.organization_id == current_user.organization_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(task, k, v)
    try:
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update task")
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ...models.assessment import Assessment
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.organization_id == current_user.organization_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    in_use = db.query(Assessment).filter(
        Assessment.task_id == task_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete task: it is used by one or more assessments",
        )
    db.delete(task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete task")
