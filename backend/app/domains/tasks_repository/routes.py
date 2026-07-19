import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from ...platform.database import get_db
from ...platform.admin_auth import require_admin_secret
from ...deps import get_current_user
from ...platform.config import settings
from ...models.user import User
from ...models.task import Task
from ...schemas.task import TaskCreate, TaskResponse, TaskUpdate
from ...services.task_repo_service import (
    recreate_task_main_repo,
    repo_file_count,
    task_main_repo_path,
    task_template_repository_name,
)
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.task_approval_service import (
    TaskApprovalError,
)
from ...services.task_catalog import (
    canonical_task_catalog_dir,
    sync_template_task_specs,
)
from ...services.task_spec_loader import load_task_specs
from .task_collection_queries import (
    apply_task_collection_filters,
    task_facets,
    task_tenant_visibility_filter,
    visible_task_filter,
)
from .task_update_policy import (
    ensure_repo_structure,
    normalize_task_payload,
    protect_system_task_metadata,
)
from .task_update_command import execute_task_update
from .task_generated_approval_command import approve_generated_task_command
from .task_role_scope import (
    lock_task_role_scope,
    reconcile_assessment_stage_changes,
    require_unlinked_task,
)
from .task_reference_guard import require_task_unreferenced

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])
_TEMPLATE_SYNC_ATTEMPTED = False


def _ensure_task_authoring_enabled() -> None:
    if settings.TASK_AUTHORING_API_ENABLED:
        return
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Task authoring via API is disabled. Tasks are backend-managed only.",
    )


def _serialize_task_response(task: Task) -> TaskResponse:
    repo_name = task_template_repository_name(task)
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
    candidate = canonical_task_catalog_dir()
    return candidate if candidate.is_dir() and any(candidate.glob("*.json")) else None


def _sync_template_task_specs_if_needed(db: Session) -> None:
    global _TEMPLATE_SYNC_ATTEMPTED
    if _TEMPLATE_SYNC_ATTEMPTED:
        return

    # Preserve current tests that rely on an empty sqlite task catalog.
    if settings.DATABASE_URL.startswith("sqlite"):
        _TEMPLATE_SYNC_ATTEMPTED = True
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

    try:
        stats = sync_template_task_specs(db, specs)
        _TEMPLATE_SYNC_ATTEMPTED = True
        if any(stats.values()):
            log = logger.warning if stats["version_required"] or stats["preserved_referenced"] else logger.info
            log("Task template sync complete: %s", stats)
    except Exception:
        db.rollback()
        logger.exception("Task template sync failed during DB update.")


class _AdminDeleteTemplateBody(BaseModel):
    task_key: str


@router.post("/admin/delete-template")
def admin_delete_template_task(
    body: _AdminDeleteTemplateBody,
    _admin: None = Depends(require_admin_secret),
    db: Session = Depends(get_db),
):
    """Delete a template task by task_key using the dedicated admin secret."""
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
        .with_for_update(of=Task)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail=f"No template task found with task_key={task_key!r}")
    require_task_unreferenced(db, task_id=int(task.id))
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
    payload = normalize_task_payload(data.model_dump())
    payload = protect_system_task_metadata(payload)
    payload = ensure_repo_structure(payload)
    task = Task(organization_id=current_user.organization_id, **payload)
    db.add(task)
    try:
        db.flush()
        from ...services.task_repository_serialization import (
            task_repository_write_mutex,
        )

        with task_repository_write_mutex(db, task_id=int(task.id)):
            recreate_task_main_repo(task)
            repo_service = AssessmentRepositoryService(
                settings.GITHUB_ORG,
                settings.GITHUB_TOKEN,
            )
            repo_service.create_template_repo(task)
            db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create task")
    return _serialize_task_response(task)


@router.get("/", response_model=List[TaskResponse])
def list_tasks(
    search: Optional[str] = Query(default=None, max_length=200),
    role: Optional[str] = Query(default=None, max_length=100),
    difficulty: Optional[str] = Query(default=None, max_length=100),
    task_type: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _sync_template_task_specs_if_needed(db)
    query = (
        db.query(Task)
        .filter(*visible_task_filter(current_user.organization_id))
    )
    query = apply_task_collection_filters(
        query,
        search=search,
        role=role,
        difficulty=difficulty,
        task_type=task_type,
    )
    tasks = query.order_by(Task.name.asc(), Task.id.asc()).offset(offset).limit(limit).all()
    return [_serialize_task_response(task) for task in tasks]


@router.get("/facets")
def list_task_facets(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return task_facets(
        db,
        organization_id=current_user.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get("/drafts", response_model=List[TaskResponse])
def list_generated_drafts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generated task drafts awaiting recruiter review.

    These are org-owned, ``is_active=False`` tasks the JD→spec generator
    authored (``extra_data.generated``). The recruiter reviews each and
    approves (activates) or rejects it. Ordered newest-first.
    """
    drafts = (
        db.query(Task)
        .filter(
            Task.organization_id == current_user.organization_id,
            Task.is_active == False,  # noqa: E712
            Task.extra_data["generated"].as_boolean().is_(True),
        )
        .order_by(Task.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_task_response(task) for task in drafts]


@router.post("/{task_id}/approve", response_model=TaskResponse)
def approve_generated_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activate a generated draft so it can be assigned to candidates.

    Provisions and verifies the exact template repository first, then sets
    ``is_active=True`` and clears ``needs_review``. Repository failure is
    fail-closed: the draft remains inactive and can be retried safely.
    """
    try:
        result = approve_generated_task_command(
            db,
            task_id=task_id,
            current_user=current_user,
        )
        task = result.task
    except TaskApprovalError as exc:
        logger.warning(
            "generated task approval blocked by repository readiness task_id=%s: %s",
            task_id,
            exc,
        )
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
                if exc.code == "task_approval_superseded"
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=exc.public_detail,
        ) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected generated task approval failure task_id=%s",
            task_id,
        )
        raise HTTPException(status_code=500, detail="Failed to approve task")
    reconcile_assessment_stage_changes(
        db,
        role_ids=result.changed_stage_role_ids,
        role_versions=result.role_versions,
    )
    return _serialize_task_response(task)


@router.delete("/{task_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_generated_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject an unapproved, unreferenced generated draft."""
    scope = lock_task_role_scope(
        db,
        task_id=task_id,
        current_user=current_user,
    )
    task = scope.task
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    if not extra.get("generated") or task.is_active:
        raise HTTPException(status_code=400, detail="Only un-approved generated drafts can be rejected")
    require_unlinked_task(scope, operation="reject")
    require_task_unreferenced(db, task_id=int(task.id))
    try:
        db.delete(task)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to reject task")
    return None


@router.get("/{task_id}/rubric")
def get_task_rubric(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return task-specific evaluator rubric criteria for recruiter scoring UI."""
    task = db.query(Task).filter(
        Task.id == task_id,
        task_tenant_visibility_filter(current_user.organization_id),
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
        task_tenant_visibility_filter(current_user.organization_id),
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
    task = execute_task_update(
        db,
        task_id=task_id,
        payload=data.model_dump(exclude_unset=True),
        current_user=current_user,
        recreate_repository=recreate_task_main_repo,
        repository_service_factory=AssessmentRepositoryService,
    )
    return _serialize_task_response(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_task_authoring_enabled()
    scope = lock_task_role_scope(
        db,
        task_id=task_id,
        current_user=current_user,
    )
    task = scope.task
    require_unlinked_task(scope, operation="delete")
    require_task_unreferenced(db, task_id=int(task.id))
    db.delete(task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete task")
