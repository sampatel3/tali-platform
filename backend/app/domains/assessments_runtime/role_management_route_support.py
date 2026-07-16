from __future__ import annotations

from sqlalchemy.orm import Session

from ...models.role import Role
from ...models.user import User
from ...platform.request_context import get_request_id
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version


def _add_role_change_boundary(
    db: Session,
    *,
    role: Role,
    current_user: User,
    action: str,
    reason: str,
    before: dict | None = None,
) -> int:
    """Advance a role revision for related shared configuration.

    Criteria, client links, and task associations live outside the ``roles``
    table but still invalidate an open job editor snapshot. Their audit event
    may therefore have an empty column diff while retaining actor/action/time.
    """

    audit_before = before if before is not None else capture_role_change_snapshot(role)
    from_version = int(role.version or 1)
    to_version = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=audit_before,
        action=action,
        actor_user_id=int(current_user.id),
        from_version=from_version,
        to_version=to_version,
        reason=reason,
        request_id=get_request_id(),
        allow_empty_changes=True,
    )
    return to_version
