"""Stable role-family ordering for the Jobs catalogue."""

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import aliased

from ...models.role import Role


def order_roles_by_family_name(query):
    """Anchor related roles to their original before limit/pagination."""
    owner_role = aliased(Role)
    family_name = func.coalesce(owner_role.name, Role.name)
    family_id = func.coalesce(owner_role.id, Role.id)
    return query.outerjoin(
        owner_role,
        and_(
            Role.ats_owner_role_id == owner_role.id,
            owner_role.organization_id == Role.organization_id,
            owner_role.deleted_at.is_(None),
        ),
    ).order_by(
        func.lower(family_name).asc(),
        family_id.asc(),
        case((Role.ats_owner_role_id.is_(None), 0), else_=1).asc(),
        func.lower(Role.name).asc(),
        Role.id.asc(),
    )


def load_role_catalogue_page(query, *, sort_by: str, limit: int | None):
    """Load a prefix without splitting the name-sorted boundary family."""
    if limit is None:
        return query.all()
    roles = query.limit(limit).all()
    if sort_by != "name" or len(roles) != limit:
        return roles
    family_id = int(roles[-1].ats_owner_role_id or roles[-1].id)
    boundary = query.filter(
        or_(Role.id == family_id, Role.ats_owner_role_id == family_id)
    ).all()
    loaded_ids = {int(role.id) for role in roles}
    roles.extend(role for role in boundary if int(role.id) not in loaded_ids)
    return roles
