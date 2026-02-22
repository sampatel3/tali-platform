import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.config import settings
from ...models.user import User
from ...models.task import Task
from ...schemas.task import TaskCreate, TaskResponse, TaskUpdate
from ...services.task_repo_service import (
    build_default_repo_structure,
    recreate_task_main_repo,
    repo_file_count,
    task_main_repo_path,
)
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.task_spec_loader import load_task_specs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])
_TEMPLATE_SYNC_ATTEMPTED = False


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


def _ensure_task_authoring_enabled() -> None:
    if settings.TASK_AUTHORING_API_ENABLED:
        return
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Task authoring via API is disabled. Tasks are backend-managed only.",
    )


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


def _resolve_tasks_dir() -> Optional[Path]:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tasks"
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate
    return None


def _build_template_task_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    task_id_str = spec.get("task_id", "unknown")
    name = spec.get("name", task_id_str)
    role = spec.get("role")
    scenario = spec.get("scenario")
    known_keys = {
        "task_id",
        "name",
        "role",
        "duration_minutes",
        "claude_budget_limit_usd",
        "calibration_prompt",
        "scenario",
        "repo_structure",
        "evaluation_rubric",
    }
    extra_data = {k: v for k, v in spec.items() if k not in known_keys}
    return {
        "organization_id": None,
        "name": name,
        "description": scenario[:500] if scenario else name,
        "task_type": role or "general",
        "difficulty": "medium",
        "duration_minutes": spec.get("duration_minutes", 30),
        "starter_code": None,
        "test_code": None,
        "is_template": True,
        "is_active": True,
        "calibration_prompt": spec.get("calibration_prompt"),
        "claude_budget_limit_usd": spec.get("claude_budget_limit_usd"),
        "task_key": task_id_str,
        "role": role,
        "scenario": scenario,
        "repo_structure": spec.get("repo_structure"),
        "evaluation_rubric": spec.get("evaluation_rubric"),
        "extra_data": extra_data or None,
    }


def _sync_template_task_specs_if_needed(db: Session) -> None:
    global _TEMPLATE_SYNC_ATTEMPTED
    if _TEMPLATE_SYNC_ATTEMPTED:
        return
    _TEMPLATE_SYNC_ATTEMPTED = True

    # Preserve current tests that rely on an empty sqlite task catalog.
    if settings.DATABASE_URL.startswith("sqlite"):
        return

    tasks_dir = _resolve_tasks_dir()
    if not tasks_dir:
        logger.warning("Task template sync skipped: tasks directory not found.")
        return

    try:
        specs = load_task_specs(tasks_dir)
    except Exception:
        logger.exception("Task template sync skipped: failed to load specs from %s", tasks_dir)
        return

    if not specs:
        return

    existing_templates = {
        task.task_key: task
        for task in db.query(Task).filter(Task.is_template == True, Task.organization_id == None).all()  # noqa: E712,E711
        if task.task_key
    }
    spec_task_keys: set[str] = set()
    created = 0
    updated = 0
    deactivated = 0

    try:
        for spec in specs:
            task_key = spec.get("task_id")
            if not task_key:
                continue
            spec_task_keys.add(task_key)

            payload = _build_template_task_payload(spec)
            existing = existing_templates.get(task_key)
            if existing is None:
                db.add(Task(**payload))
                created += 1
                continue

            has_changes = False
            for field, value in payload.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    has_changes = True
            if has_changes:
                updated += 1

        # Soft-delete stale templates removed from tasks/*.json
        for task_key, task in existing_templates.items():
            if task_key in spec_task_keys:
                continue
            if task.is_active:
                task.is_active = False
                deactivated += 1

        if created or updated or deactivated:
            db.commit()
            logger.info(
                "Task template sync complete: created=%d updated=%d deactivated=%d",
                created,
                updated,
                deactivated,
            )
    except Exception:
        db.rollback()
        logger.exception("Task template sync failed during DB update.")


class _AdminDeleteTemplateBody(BaseModel):
    task_key: str


@router.post("/admin/delete-template")
def admin_delete_template_task(
    body: _AdminDeleteTemplateBody,
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    """Delete a template task by task_key. Requires X-Admin-Secret header (SECRET_KEY)."""
    if not x_admin_secret or x_admin_secret.strip() != (settings.SECRET_KEY or "").strip():
        raise HTTPException(status_code=403, detail="Forbidden")
    task_key = (body.task_key or "").strip()
    if not task_key:
        raise HTTPException(status_code=400, detail="task_key required")
    task = (
        db.query(Task)
        .filter(
            Task.task_key == task_key,
            Task.is_template == True,
            Task.organization_id == None,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail=f"No template task found with task_key={task_key!r}")
    db.delete(task)
    db.commit()
    return {"status": "ok", "message": f"Deleted template task task_key={task_key!r}"}


# --- Standard CRUD ---

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_task_authoring_enabled()
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
    _sync_template_task_specs_if_needed(db)
    tasks = (
        db.query(Task)
        .filter(Task.is_active == True)  # noqa: E712
        .filter((Task.organization_id == current_user.organization_id) | (Task.is_template == True))
        .all()
    )
    return [_serialize_task_response(task) for task in tasks]


@router.get("/{task_id}/rubric")
def get_task_rubric(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return task-specific evaluator rubric criteria for recruiter scoring UI."""
    task = db.query(Task).filter(
        Task.id == task_id,
        (Task.organization_id == current_user.organization_id) | (Task.is_template == True),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    rubric = task.evaluation_rubric if isinstance(task.evaluation_rubric, dict) else {}
    return {
        "task_id": task.id,
        "task_key": task.task_key,
        "task_name": task.name,
        "evaluation_rubric": rubric,
    }


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
    _ensure_task_authoring_enabled()
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
    _ensure_task_authoring_enabled()
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
