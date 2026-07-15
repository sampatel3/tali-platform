"""Optimistic-concurrency helpers for shared Role mutations.

The row lock serializes the compare-and-write boundary on Postgres.  The
explicit version check is what turns a stale browser tab into a truthful 409
instead of a successful last-write-wins overwrite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

from ..models.role import Role


ROLE_VERSION_CONFLICT = "ROLE_VERSION_CONFLICT"


def role_query_for_update(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
) -> Query:
    """Return the tenant-scoped live-role query with a write lock."""

    return (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
    )


def assert_role_version(
    role: Role,
    *,
    expected_version: int,
    current_role: Any | Callable[[], Any] | None = None,
    changed_by: dict[str, Any] | Callable[[], dict[str, Any] | None] | None = None,
) -> None:
    """Raise the stable 409 contract when a caller's snapshot is stale.

    Conflict metadata may be supplied lazily so successful writes do not pay
    for a full role serialization or latest-actor audit query.
    """

    current_version = int(getattr(role, "version", 1) or 1)
    if int(expected_version) == current_version:
        return
    resolved_current_role = current_role() if callable(current_role) else current_role
    resolved_changed_by = changed_by() if callable(changed_by) else changed_by
    raise HTTPException(
        status_code=409,
        detail={
            "code": ROLE_VERSION_CONFLICT,
            "message": (
                "This job changed after you opened it. Review the latest "
                "version before saving your changes."
            ),
            "current_version": current_version,
            "current_role": resolved_current_role,
            "changed_by": resolved_changed_by,
        },
    )


def bump_role_version(role: Role) -> int:
    """Increment once after all changes in the caller-owned transaction."""

    next_version = int(getattr(role, "version", 1) or 1) + 1
    role.version = next_version
    return next_version


__all__ = [
    "ROLE_VERSION_CONFLICT",
    "assert_role_version",
    "bump_role_version",
    "role_query_for_update",
]
