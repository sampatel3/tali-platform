"""Tenant-safe, lightweight role-family loading and serialization."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, joinedload, selectinload, with_loader_criteria
from sqlalchemy.orm.attributes import NO_VALUE

from ...models.role import Role
from ...schemas.role import RoleFamilyResponse, RoleReference


def _loaded_relationship_items(
    entity: Any, relationship_name: str
) -> list[Any] | None:
    try:
        loaded = getattr(sa_inspect(entity).attrs, relationship_name).loaded_value
    except Exception:
        return None
    if loaded is NO_VALUE:
        return None
    return list(loaded or [])


_ROLE_REFERENCE_COLUMNS = (
    Role.id,
    Role.organization_id,
    Role.name,
    Role.role_kind,
    Role.ats_owner_role_id,
    Role.workable_job_id,
    Role.deleted_at,
)


def role_family_load_options(*, organization_id: int | None = None):
    """Load complete families without hydrating every sibling's full job spec."""

    options = (
        joinedload(Role.ats_owner_role)
        .selectinload(Role.sister_roles)
        .load_only(*_ROLE_REFERENCE_COLUMNS),
        selectinload(Role.sister_roles).load_only(*_ROLE_REFERENCE_COLUMNS),
    )
    if organization_id is None:
        return options
    return (
        *options,
        with_loader_criteria(
            Role,
            Role.organization_id == int(organization_id),
            include_aliases=True,
        ),
    )


def roles_with_families(
    db: Session,
    role_ids: list[int],
    *,
    organization_id: int,
) -> dict[int, Role]:
    """Batch-load named role families for an already authorized set of IDs."""

    ids = sorted({int(role_id) for role_id in role_ids})
    if not ids:
        return {}
    roles = (
        db.query(Role)
        .options(*role_family_load_options(organization_id=organization_id))
        .filter(
            Role.id.in_(ids),
            Role.organization_id == int(organization_id),
        )
        .all()
    )
    return {int(role.id): role for role in roles}


def role_family_response(role: Role) -> RoleFamilyResponse:
    """Return the complete named family for a standard or related role."""

    role_kind = str(getattr(role, "role_kind", None) or "standard")
    organization_id = getattr(role, "organization_id", None)

    def same_organization(item: Any) -> bool:
        return (
            organization_id is None
            or getattr(item, "organization_id", None) == organization_id
        )

    candidate_owner = (
        getattr(role, "ats_owner_role", None) if role_kind == "sister" else role
    )
    owner = (
        candidate_owner
        if candidate_owner is not None
        and same_organization(candidate_owner)
        and getattr(candidate_owner, "deleted_at", None) is None
        else role
    )
    related = _loaded_relationship_items(owner, "sister_roles")
    if related is None:
        try:
            related = list(getattr(owner, "sister_roles", None) or [])
        except Exception:
            related = []
    if role_kind == "sister" and all(
        int(getattr(item, "id", 0) or 0) != int(role.id) for item in related
    ):
        related.append(role)

    unique_related: dict[int, Role] = {}
    for item in related:
        item_id = int(getattr(item, "id", 0) or 0)
        if (
            not item_id
            or item_id == int(owner.id)
            or not same_organization(item)
            or getattr(item, "deleted_at", None) is not None
        ):
            continue
        unique_related[item_id] = item
    ordered_related = sorted(
        unique_related.values(),
        key=lambda item: (
            str(getattr(item, "name", "") or "").casefold(),
            int(getattr(item, "id", 0) or 0),
        ),
    )
    return RoleFamilyResponse(
        owner=RoleReference(id=int(owner.id), name=str(owner.name)),
        related=[
            RoleReference(id=int(item.id), name=str(item.name))
            for item in ordered_related
        ],
    )


__all__ = [
    "_loaded_relationship_items",
    "role_family_load_options",
    "role_family_response",
    "roles_with_families",
]
