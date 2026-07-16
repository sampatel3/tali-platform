"""Repository-serialized entrypoint for durable role activation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from .role_activation_completion import _complete_role_activation_intent_serialized
from .role_activation_task_guard import activation_intent_state, intent_task
from .task_repository_serialization import task_repository_write_mutex


def _activation_repository_task_hint(
    db: Session,
    *,
    role_id: int,
    request_id: str,
) -> int | None:
    role = (
        db.query(Role)
        .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
        .one_or_none()
    )
    if role is None:
        return None
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id or ""):
        return None
    task = intent_task(role, intent)
    return int(task.id) if task is not None else None


def complete_role_activation_intent(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    worker_task_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Serialize repository writes before the canonical activation capture."""

    task_id = _activation_repository_task_hint(
        db,
        role_id=int(role_id),
        request_id=request_id,
    )
    db.rollback()
    if task_id is None:
        return _complete_role_activation_intent_serialized(
            db,
            role_id=role_id,
            request_id=request_id,
            worker_task_id=worker_task_id,
            now=now,
        )
    with task_repository_write_mutex(db, task_id=task_id):
        return _complete_role_activation_intent_serialized(
            db,
            role_id=role_id,
            request_id=request_id,
            worker_task_id=worker_task_id,
            now=now,
            repository_mutex_task_id=task_id,
        )


__all__ = ["complete_role_activation_intent"]
