"""P1: role publishing — lifecycle status + public slug.

Publishing a role makes it eligible for the public careers site: it flips
``status`` to 'published' and assigns a unique per-org ``slug`` used in the public
URL (/careers/:org/:role-slug) and JobPosting JSON-LD. Does NOT commit — caller
owns the transaction.
"""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.role import (
    ROLE_STATUS_DRAFT,
    ROLE_STATUS_PUBLISHED,
    ROLE_STATUSES,
    Role,
)


def slugify_role(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _unique_slug(
    db: Session, organization_id: int, base: str, role_id: int | None
) -> str:
    base = base or "role"
    slug = base
    suffix = 2
    while True:
        clash = (
            db.query(Role.id)
            .filter(
                Role.organization_id == organization_id,
                Role.slug == slug,
                Role.id != (role_id if role_id is not None else -1),
            )
            .first()
        )
        if not clash:
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def set_role_status(db: Session, role: Role, status: str) -> Role:
    if status not in ROLE_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unsupported role status={status!r}")
    role.status = status
    db.flush()
    return role


def publish_role(db: Session, role: Role, *, slug: str | None = None) -> Role:
    """Publish a role: assign a unique slug (from ``slug``, else the existing
    slug, else the role name) and set status to 'published'."""
    desired = slugify_role(slug or role.slug or role.name)
    role.slug = _unique_slug(db, role.organization_id, desired, role.id)
    role.status = ROLE_STATUS_PUBLISHED
    db.flush()
    return role


def unpublish_role(db: Session, role: Role) -> Role:
    """Return a role to draft (removes it from the public careers site). The slug
    is retained so re-publishing keeps the same public URL."""
    role.status = ROLE_STATUS_DRAFT
    db.flush()
    return role
