from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


PERSISTED_TASK_SPEC_KEYS = {
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
TASK_CATALOG_SYNC_LOCK_SCOPE = "template_task_catalog_sync"


def serialize_task_catalog_sync(db: Session) -> None:
    """Serialize catalogue discovery+write across PostgreSQL web workers."""

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope), 0)"),
        {"scope": TASK_CATALOG_SYNC_LOCK_SCOPE},
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def canonical_task_catalog_dir() -> Path:
    return backend_root() / "tasks"


def _task_attr(task: Any, key: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def task_workspace_root_name(task: Any) -> str:
    repo_structure = _task_attr(task, "repo_structure") or {}
    root_name = (
        str((repo_structure or {}).get("name") or "").strip()
        or str(_task_attr(task, "task_key", "") or _task_attr(task, "task_id", "") or _task_attr(task, "id", "")).strip()
        or "assessment-task"
    )
    safe_root = re.sub(r"[^a-zA-Z0-9._-]+", "-", root_name).strip("-")
    if (
        safe_root
        and safe_root == root_name
        and safe_root not in {".", ".."}
        and safe_root.casefold() != ".git"
        and len(safe_root) <= 100
    ):
        return safe_root

    # Unsafe, lossy, and overlong names use a digest-qualified component so
    # `/workspace/..`, `/workspace/.git`, and slug collisions are impossible.
    digest = hashlib.sha256(root_name.encode("utf-8")).hexdigest()[:16]
    readable = safe_root.strip("-.")[:75].rstrip("-.") or "assessment-task"
    return f"{readable}-{digest}"


def workspace_repo_root(task: Any) -> str:
    return f"/workspace/{task_workspace_root_name(task)}"


def build_template_task_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    task_id_str = spec.get("task_id", "unknown")
    name = spec.get("name", task_id_str)
    role = spec.get("role")
    scenario = spec.get("scenario")
    extra_data = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
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


def _lock_existing_template(
    db: Session,
    *,
    task_id: int,
    task_key: str,
) -> Any | None:
    """Refresh and lock the exact template before changing catalogue state."""
    from ..models.task import Task

    return (
        db.query(Task)
        .filter(
            Task.id == int(task_id),
            Task.task_key == task_key,
            Task.is_template == True,  # noqa: E712
            Task.organization_id == None,  # noqa: E711
        )
        .populate_existing()
        .with_for_update(of=Task)
        .one_or_none()
    )


def _template_task_is_referenced(db: Session, *, task_id: int) -> bool:
    """Re-query every persistent reference while the Task row is locked."""
    from ..domains.tasks_repository.task_reference_guard import task_reference_kinds

    return bool(task_reference_kinds(db, task_id=int(task_id)))


def sync_template_task_specs(db: Session, specs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    from ..models.task import Task

    # This must precede discovery: a second worker waits here, then re-queries
    # after the first worker commits instead of inserting the same non-unique
    # task_key. Existing duplicate rows are deliberately left untouched.
    serialize_task_catalog_sync(db)
    existing_templates = {
        task.task_key: task
        for task in db.query(Task).filter(Task.is_template == True, Task.organization_id == None).all()  # noqa: E712,E711
        if task.task_key
    }
    spec_task_keys: set[str] = set()
    stats = {
        "created": 0,
        "updated": 0,
        "deactivated": 0,
        "preserved_referenced": 0,
        "version_required": 0,
    }

    for spec in specs:
        task_key = spec.get("task_id")
        if not task_key:
            continue
        spec_task_keys.add(task_key)

        payload = build_template_task_payload(spec)
        existing = existing_templates.get(task_key)
        if existing is None:
            db.add(Task(**payload))
            stats["created"] += 1
            continue

        existing = _lock_existing_template(
            db,
            task_id=int(existing.id),
            task_key=str(task_key),
        )
        if existing is None:
            # The discovered row changed identity or disappeared before its
            # lock. Treat this spec as missing; never overwrite a different
            # task based on the stale discovery snapshot.
            db.add(Task(**payload))
            stats["created"] += 1
            continue

        changes = {
            field: value
            for field, value in payload.items()
            if getattr(existing, field) != value
        }
        if changes and _template_task_is_referenced(
            db,
            task_id=int(existing.id),
        ):
            stats["version_required"] += 1
            continue
        for field, value in changes.items():
            setattr(existing, field, value)
        if changes:
            stats["updated"] += 1

    for task_key, task in existing_templates.items():
        if task_key in spec_task_keys:
            continue
        if task.is_active:
            locked_task = _lock_existing_template(
                db,
                task_id=int(task.id),
                task_key=str(task_key),
            )
            if locked_task is None or not locked_task.is_active:
                continue
            if _template_task_is_referenced(
                db,
                task_id=int(locked_task.id),
            ):
                stats["preserved_referenced"] += 1
            else:
                locked_task.is_active = False
                stats["deactivated"] += 1

    db.commit()
    return stats
