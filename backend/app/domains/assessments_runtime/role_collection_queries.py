"""Search helpers for bounded role collection reads."""

from sqlalchemy import func, or_

from ...models.role import Role, role_tasks


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


def role_relationship_counts(db, role_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    if not role_ids:
        return {}, {}
    task_rows = (
        db.query(role_tasks.c.role_id, func.count(role_tasks.c.task_id))
        .filter(role_tasks.c.role_id.in_(role_ids))
        .group_by(role_tasks.c.role_id)
        .all()
    )
    sister_rows = (
        db.query(Role.ats_owner_role_id, func.count(Role.id))
        .filter(Role.ats_owner_role_id.in_(role_ids))
        .group_by(Role.ats_owner_role_id)
        .all()
    )
    return (
        {int(role_id): int(count) for role_id, count in task_rows},
        {int(role_id): int(count) for role_id, count in sister_rows},
    )


def count_roles(db, *, organization_id: int, search: str | None = None) -> int:
    query = db.query(func.count(Role.id)).filter(
        Role.organization_id == organization_id,
        Role.deleted_at.is_(None),
    )
    return int(apply_role_search(query, search).scalar() or 0)


__all__ = ["apply_role_search", "count_roles", "role_relationship_counts"]
