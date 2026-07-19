"""Bounded SQL metadata for the task catalogue."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...models.task import Task

ROLE_VALUE = func.coalesce(func.nullif(func.trim(Task.role), ""), "General engineering")
DIFFICULTY_VALUE = func.lower(func.coalesce(func.nullif(func.trim(Task.difficulty), ""), "medium"))
TYPE_VALUE = func.lower(func.coalesce(func.nullif(func.trim(Task.task_type), ""), "repo"))


def task_tenant_visibility_filter(organization_id: int):
    """Keep org-owned templates private; only NULL-org templates are global."""
    return or_(
        Task.organization_id == organization_id,
        (Task.organization_id.is_(None) & Task.is_template.is_(True)),
    )


def visible_task_filter(organization_id: int):
    return (Task.is_active.is_(True), task_tenant_visibility_filter(organization_id))


def apply_task_collection_filters(
    query,
    *,
    search: str | None,
    role: str | None,
    difficulty: str | None,
    task_type: str | None,
):
    if search and (term := search.strip()):
        pattern = f"%{term}%"
        query = query.filter(or_(
            Task.name.ilike(pattern),
            Task.task_key.ilike(pattern),
            Task.description.ilike(pattern),
            Task.scenario.ilike(pattern),
            ROLE_VALUE.ilike(pattern),
            DIFFICULTY_VALUE.ilike(pattern),
            TYPE_VALUE.ilike(pattern),
        ))
    if role:
        query = query.filter(ROLE_VALUE == role)
    if difficulty:
        query = query.filter(DIFFICULTY_VALUE == difficulty.lower())
    if task_type:
        query = query.filter(TYPE_VALUE == task_type.lower())
    return query


def task_facets(
    db: Session,
    *,
    organization_id: int,
    limit: int,
    offset: int,
) -> dict:
    filters = visible_task_filter(organization_id)

    def page(column) -> tuple[list[str], bool]:
        rows = (
            db.query(column)
            .filter(*filters, column.is_not(None), func.length(func.trim(column)) > 0)
            .distinct()
            .order_by(column.asc())
            .offset(offset)
            .limit(limit + 1)
            .all()
        )
        values = [str(value).strip() for (value,) in rows[:limit]]
        return values, len(rows) > limit

    roles, more_roles = page(ROLE_VALUE)
    difficulties, more_difficulties = page(DIFFICULTY_VALUE)
    task_types, more_types = page(TYPE_VALUE)
    has_more = more_roles or more_difficulties or more_types
    return {
        "roles": roles,
        "difficulties": difficulties,
        "task_types": task_types,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


__all__ = [
    "apply_task_collection_filters",
    "task_facets",
    "task_tenant_visibility_filter",
    "visible_task_filter",
]
