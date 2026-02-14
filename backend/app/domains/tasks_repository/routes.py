import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.config import settings
from ...models.user import User
from ...models.task import Task
from ...schemas.task import TaskCreate, TaskResponse, TaskUpdate
from ...domains.integrations_notifications.adapters import build_claude_adapter
from ...services.task_repo_service import (
    build_default_repo_structure,
    recreate_task_main_repo,
    repo_file_count,
    task_main_repo_path,
)
from ...services.assessment_repository_service import AssessmentRepositoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _normalize_task_payload(payload: dict) -> dict:
    """Map alternate task payload keys into persisted model fields."""
    normalized = dict(payload)

    expected_insights = normalized.pop("expected_insights", None)
    valid_solutions = normalized.pop("valid_solutions", None)
    expected_approaches = normalized.pop("expected_approaches", None)

    if expected_insights is not None or valid_solutions is not None or expected_approaches is not None:
        extra_data = normalized.get("extra_data") or {}
        if expected_insights is not None:
            extra_data["expected_insights"] = expected_insights
        if valid_solutions is not None:
            extra_data["valid_solutions"] = valid_solutions
        if expected_approaches is not None:
            extra_data["expected_approaches"] = expected_approaches
        normalized["extra_data"] = extra_data

    return normalized


DEFAULT_EVALUATION_RUBRIC: Dict[str, Dict[str, Any]] = {
    "problem_solving": {
        "weight": 0.35,
        "what_to_look_for": "Breaks down ambiguity, validates assumptions, and drives to root cause.",
    },
    "code_quality": {
        "weight": 0.30,
        "what_to_look_for": "Readable, maintainable implementation with clear naming and structure.",
    },
    "testing_and_validation": {
        "weight": 0.20,
        "what_to_look_for": "Uses targeted tests/checks, verifies edge cases, and confirms behavior.",
    },
    "ai_collaboration": {
        "weight": 0.15,
        "what_to_look_for": "Uses AI effectively with concrete prompts and independent verification.",
    },
}

SUITABLE_ROLES_BY_TYPE: Dict[str, List[str]] = {
    "debugging": ["backend engineer", "full-stack engineer", "platform engineer"],
    "ai_engineering": ["ai engineer", "ml engineer", "applied ai engineer"],
    "optimization": ["backend engineer", "data engineer", "performance engineer"],
    "build": ["full-stack engineer", "backend engineer", "software engineer"],
    "refactor": ["backend engineer", "staff engineer", "software engineer"],
}


def _default_role_for_type(task_type: str | None) -> str:
    task_type_norm = (task_type or "").strip().lower()
    if task_type_norm == "ai_engineering":
        return "ai_engineer"
    if task_type_norm == "optimization":
        return "platform_engineer"
    return "software_engineer"


def _default_suitable_roles(task_type: str | None, role: str | None = None) -> List[str]:
    roles = list(SUITABLE_ROLES_BY_TYPE.get((task_type or "").strip().lower(), ["software engineer"]))
    if role:
        normalized_role = role.replace("_", " ").strip().lower()
        if normalized_role and normalized_role not in roles:
            roles.insert(0, normalized_role)
    return roles


def _ensure_repo_structure(payload: Dict[str, Any], fallback_task: Optional[Task] = None) -> Dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("repo_structure"):
        return normalized

    starter_in_payload = "starter_code" in normalized
    test_in_payload = "test_code" in normalized
    if (
        fallback_task is not None
        and getattr(fallback_task, "repo_structure", None)
        and not starter_in_payload
        and not test_in_payload
    ):
        return normalized

    starter_code = normalized.get("starter_code")
    test_code = normalized.get("test_code")
    if fallback_task is not None:
        starter_code = starter_code if starter_code is not None else getattr(fallback_task, "starter_code", None)
        test_code = test_code if test_code is not None else getattr(fallback_task, "test_code", None)

    if not starter_code and not test_code:
        return normalized

    task_name = normalized.get("name") or (getattr(fallback_task, "name", None) if fallback_task is not None else None)
    scenario = normalized.get("scenario") or normalized.get("description") or (
        getattr(fallback_task, "scenario", None) if fallback_task is not None else None
    )
    normalized["repo_structure"] = build_default_repo_structure(
        starter_code,
        test_code,
        task_name=task_name,
        scenario=scenario,
    )
    return normalized


def _serialize_task_response(task: Task) -> TaskResponse:
    raw = getattr(task, "task_key", None) or getattr(task, "id", "task")
    repo_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(raw)).strip("-").lower() or "task"
    template_repo_url = (
        f"mock://{settings.GITHUB_ORG}/{repo_name}"
        if settings.GITHUB_MOCK_MODE
        else f"https://github.com/{settings.GITHUB_ORG}/{repo_name}.git"
    )
    payload = TaskResponse.model_validate(task).model_dump()
    payload["main_repo_path"] = task_main_repo_path(task)
    payload["template_repo_url"] = template_repo_url
    payload["repo_file_count"] = repo_file_count(getattr(task, "repo_structure", None))
    return TaskResponse.model_validate(payload)


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
    claude_budget_limit_usd: Optional[float] = None
    starter_code: str
    test_code: str
    task_key: Optional[str] = None
    role: Optional[str] = None
    scenario: Optional[str] = None
    repo_structure: Optional[Dict[str, Any]] = None
    evaluation_rubric: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None


# --- AI generation endpoint ---

TASK_GEN_SYSTEM = """You are an expert technical assessment designer for software engineering roles.
Given a description of what the employer wants to test, generate a complete coding assessment task.

You MUST respond with ONLY a valid JSON object (no markdown, no ```json blocks).
Required keys:
- name: string — short task title (e.g. "Async Pipeline Debugging")
- description: string — 2-4 sentence description of what the candidate must do, written for the candidate
- task_type: string — one of: debugging, ai_engineering, optimization, build, refactor
- difficulty: string — one of: junior, mid, senior, staff
- duration_minutes: integer — recommended time (15-90)
- starter_code: string — complete Python starter code with intentional issues or scaffolding (20-80 lines, include comments explaining what's expected)
- test_code: string — pytest test suite that validates the solution (include 3-6 tests)

Optional keys (include when possible):
- task_key: short stable key (snake_case)
- role: best-fit role key (e.g. backend_engineer, data_engineer, ai_engineer)
- scenario: concise real-world context (2-5 sentences)
- repo_structure: object with `name` and `files` map where files include at least README.md, src/task.py, tests/test_task.py
- evaluation_rubric: object where each key is a rubric category and value includes `weight` and `what_to_look_for`
- extra_data: object with `suitable_roles` (array of role labels) and `skills_tested` (array)
- claude_budget_limit_usd: number (optional budget cap in USD for candidate Claude prompts)

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

    claude = build_claude_adapter()

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
    role = generated.get("role") or _default_role_for_type(generated.get("task_type"))
    scenario = generated.get("scenario") or generated.get("description")
    repo_structure = generated.get("repo_structure") or build_default_repo_structure(
        generated.get("starter_code"),
        generated.get("test_code"),
        task_name=generated.get("name"),
        scenario=scenario,
    )
    evaluation_rubric = generated.get("evaluation_rubric") or DEFAULT_EVALUATION_RUBRIC
    extra_data = generated.get("extra_data") if isinstance(generated.get("extra_data"), dict) else {}
    extra_data.setdefault("suitable_roles", _default_suitable_roles(generated.get("task_type"), role))
    if not extra_data.get("skills_tested"):
        extra_data["skills_tested"] = [k.replace("_", " ") for k in evaluation_rubric.keys()]
    extra_data["generated_by"] = "genai"

    response_payload = {k: generated[k] for k in required}
    response_payload.update(
        {
            "claude_budget_limit_usd": generated.get("claude_budget_limit_usd"),
            "task_key": generated.get("task_key"),
            "role": role,
            "scenario": scenario,
            "repo_structure": repo_structure,
            "evaluation_rubric": evaluation_rubric,
            "extra_data": extra_data,
        }
    )
    return TaskGenerateResponse(**response_payload)


# --- Standard CRUD ---

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = _normalize_task_payload(data.model_dump())
    payload = _ensure_repo_structure(payload)
    task = Task(organization_id=current_user.organization_id, **payload)
    db.add(task)
    try:
        db.flush()
        recreate_task_main_repo(task)
        repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
        repo_service.create_template_repo(task)
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create task")
    return _serialize_task_response(task)


@router.get("/", response_model=List[TaskResponse])
def list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tasks = db.query(Task).filter(
        (Task.organization_id == current_user.organization_id) | (Task.is_template == True)
    ).all()
    return [_serialize_task_response(task) for task in tasks]


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
    return _serialize_task_response(task)


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
    update_data = _normalize_task_payload(data.model_dump(exclude_unset=True))
    update_data = _ensure_repo_structure(update_data, fallback_task=task)
    for k, v in update_data.items():
        setattr(task, k, v)
    try:
        db.flush()
        recreate_task_main_repo(task)
        repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
        repo_service.create_template_repo(task)
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update task")
    return _serialize_task_response(task)


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
