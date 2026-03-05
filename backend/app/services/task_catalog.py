from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable

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
    return safe_root or "assessment-task"


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


def sync_template_task_specs(db: Session, specs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    from ..models.assessment import Assessment
    from ..models.task import Task

    existing_templates = {
        task.task_key: task
        for task in db.query(Task).filter(Task.is_template == True, Task.organization_id == None).all()  # noqa: E712,E711
        if task.task_key
    }
    referenced_task_ids = {
        int(task_id)
        for (task_id,) in db.query(Assessment.task_id).filter(Assessment.task_id.isnot(None)).distinct().all()
        if task_id is not None
    }

    spec_task_keys: set[str] = set()
    stats = {
        "created": 0,
        "updated": 0,
        "deactivated": 0,
        "preserved_referenced": 0,
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

        has_changes = False
        for field, value in payload.items():
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                has_changes = True
        if has_changes:
            stats["updated"] += 1

    for task_key, task in existing_templates.items():
        if task_key in spec_task_keys:
            continue
        if task.is_active:
            task.is_active = False
            if task.id in referenced_task_ids:
                stats["preserved_referenced"] += 1
            else:
                stats["deactivated"] += 1

    db.commit()
    return stats
