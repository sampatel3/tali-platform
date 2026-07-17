"""Stable role-family ordering for the Jobs catalogue."""

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from ...models.role import Role

NAME_CATALOGUE_SORTS = frozenset({"name", "agent_on_name"})


def order_roles_by_family_name(
    query,
    *,
    organization_id: int,
    agent_on_first: bool = False,
):
    """Anchor related roles to their original before limit/pagination.

    The Jobs catalogue can optionally promote agent-on families before
    applying the same stable A-Z ordering. Family-level promotion matters: a
    related role may run its own agent, but its original and siblings must stay
    adjacent in the catalogue and across the first-page boundary.
    """
    owner_role = aliased(Role)
    family_name = func.coalesce(owner_role.name, Role.name)
    family_id = func.coalesce(owner_role.id, Role.id)
    query = query.outerjoin(
        owner_role,
        and_(
            Role.ats_owner_role_id == owner_role.id,
            owner_role.organization_id == Role.organization_id,
            owner_role.deleted_at.is_(None),
        ),
    )
    order_columns = [
        func.lower(family_name).asc(),
        family_id.asc(),
        case((Role.ats_owner_role_id.is_(None), 0), else_=1).asc(),
        func.lower(Role.name).asc(),
        Role.id.asc(),
    ]
    if agent_on_first:
        agent_on_role = aliased(Role)
        agent_on_family_id = func.coalesce(
            agent_on_role.ats_owner_role_id,
            agent_on_role.id,
        )
        agent_on_filters = [
            agent_on_role.organization_id == organization_id,
            agent_on_role.deleted_at.is_(None),
            agent_on_role.agentic_mode_enabled.is_(True),
            agent_on_role.agent_paused_at.is_(None),
        ]
        agent_on_families = (
            select(
                agent_on_role.organization_id.label("organization_id"),
                agent_on_family_id.label("family_id"),
            )
            .where(*agent_on_filters)
            .distinct()
            .subquery("agent_on_role_families")
        )
        query = query.outerjoin(
            agent_on_families,
            and_(
                agent_on_families.c.organization_id == Role.organization_id,
                agent_on_families.c.family_id == family_id,
            ),
        )
        row_agent_on = and_(
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
        )
        order_columns.insert(
            0,
            case(
                (
                    or_(agent_on_families.c.family_id.isnot(None), row_agent_on),
                    0,
                ),
                else_=1,
            ).asc(),
        )
    return query.order_by(*order_columns)


def load_role_catalogue_page(query, *, sort_by: str, limit: int | None):
    """Load a prefix without splitting the name-sorted boundary family."""
    if limit is None:
        return query.all()
    roles = query.limit(limit).all()
    if sort_by not in NAME_CATALOGUE_SORTS or len(roles) != limit:
        return roles
    boundary_role = roles[-1]
    owner_role = getattr(boundary_role, "ats_owner_role", None)
    owner_is_live = (
        owner_role is not None
        and owner_role.deleted_at is None
        and owner_role.organization_id == boundary_role.organization_id
    )
    if boundary_role.ats_owner_role_id is None:
        family_id = int(boundary_role.id)
        include_related = True
    elif owner_is_live:
        family_id = int(owner_role.id)
        include_related = True
    else:
        # The sorter treats a role whose owner is unavailable as standalone.
        # Keep the page boundary identical so later orphan siblings are not
        # appended out of full-list order.
        family_id = int(boundary_role.id)
        include_related = False
    family_filter = Role.id == family_id
    if include_related:
        family_filter = or_(family_filter, Role.ats_owner_role_id == family_id)
    boundary = query.filter(family_filter).all()
    loaded_ids = {int(role.id) for role in roles}
    roles.extend(role for role in boundary if int(role.id) not in loaded_ids)
    return roles
