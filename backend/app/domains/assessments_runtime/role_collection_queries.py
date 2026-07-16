"""Search helpers for bounded role collection reads."""

from sqlalchemy import case, func, or_

from ...models.role import Role, role_tasks
from ...models.task import Task


def apply_role_search(query, raw_search: str | None):
    term = (raw_search or "").strip()
    if not term:
        return query
    pattern = f"%{term}%"
    return query.filter(or_(
        Role.name.ilike(pattern),
        Role.description.ilike(pattern),
        Role.location_city.ilike(pattern),
        Role.location_country.ilike(pattern),
        Role.department.ilike(pattern),
        Role.employment_type.ilike(pattern),
        Role.workplace_type.ilike(pattern),
    ))


def role_task_counts(
    db, role_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    if not role_ids:
        return {}, {}
    task_rows = (
        db.query(
            role_tasks.c.role_id,
            func.count(role_tasks.c.task_id),
            func.sum(case((Task.is_active.is_(True), 1), else_=0)),
        )
        .join(Task, Task.id == role_tasks.c.task_id)
        .filter(role_tasks.c.role_id.in_(role_ids))
        .group_by(role_tasks.c.role_id)
        .all()
    )
    return (
        {int(role_id): int(count) for role_id, count, _active in task_rows},
        {
            int(role_id): int(active_count or 0)
            for role_id, _count, active_count in task_rows
        },
    )


def role_relationship_counts(
    db, role_ids: list[int]
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    if not role_ids:
        return {}, {}, {}
    task_counts, active_task_counts = role_task_counts(db, role_ids)
    sister_rows = (
        db.query(Role.ats_owner_role_id, func.count(Role.id))
        .filter(Role.ats_owner_role_id.in_(role_ids))
        .group_by(Role.ats_owner_role_id)
        .all()
    )
    return (
        task_counts,
        {int(role_id): int(count) for role_id, count in sister_rows},
        active_task_counts,
    )


def count_roles(db, *, organization_id: int, search: str | None = None) -> int:
    query = db.query(func.count(Role.id)).filter(
        Role.organization_id == organization_id,
        Role.deleted_at.is_(None),
    )
    return int(apply_role_search(query, search).scalar() or 0)


__all__ = [
    "apply_role_search",
    "count_roles",
    "role_relationship_counts",
    "role_task_counts",
]
