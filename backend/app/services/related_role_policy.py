"""Role-family policy transitions shared by related-role creation paths."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.role import ROLE_KIND_SISTER, Role
from .role_change_audit import (
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from .role_concurrency import bump_role_version


def disable_owner_auto_reject_for_new_family(
    db: Session,
    *,
    source: Role,
    creator_user_id: int,
) -> bool:
    """Turn off automatic rejects when ``source`` first shares its ATS app."""

    existing_family_member = (
        db.query(Role.id)
        .filter(
            Role.organization_id == int(source.organization_id),
            Role.ats_owner_role_id == int(source.id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if existing_family_member is not None or not (
        bool(source.auto_reject) or bool(source.auto_reject_pre_screen)
    ):
        return False

    audit_before = capture_role_change_snapshot(source)
    audit_from_version = int(source.version or 1)
    source.auto_reject = False
    source.auto_reject_pre_screen = False
    audit_to_version = bump_role_version(source)
    add_role_change_event(
        db,
        role=source,
        before=audit_before,
        action=ROLE_CHANGE_ACTION_UPDATED,
        actor_user_id=int(creator_user_id),
        from_version=audit_from_version,
        to_version=audit_to_version,
        reason=(
            "Automatic rejection disabled when the first related role joined "
            "this shared ATS candidate pool"
        ),
    )
    return True


__all__ = ["disable_owner_auto_reject_for_new_family"]
