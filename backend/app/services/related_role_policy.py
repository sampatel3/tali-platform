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
    ignore_related_role_id: int | None = None,
) -> bool:
    """Fence every family addition and disable unsafe first-family rejects."""

    existing_family_member_query = (
        db.query(Role.id)
        .filter(
            Role.organization_id == int(source.organization_id),
            Role.ats_owner_role_id == int(source.id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.deleted_at.is_(None),
        )
    )
    if ignore_related_role_id is not None:
        existing_family_member_query = existing_family_member_query.filter(
            Role.id != int(ignore_related_role_id)
        )
    existing_family_member = existing_family_member_query.first()
    first_family_member = existing_family_member is None
    disables_auto_reject = bool(
        source.auto_reject or source.auto_reject_pre_screen
    )
    audit_before = capture_role_change_snapshot(source)
    audit_from_version = int(source.version or 1)
    if disables_auto_reject:
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
            (
                "Automatic rejection disabled when the first related role joined "
                "this shared ATS candidate pool"
            )
            if disables_auto_reject and first_family_member
            else (
                "Unsafe automatic rejection repaired while a related role joined "
                "this existing shared ATS candidate pool"
                if disables_auto_reject
                else "Related role joined this shared ATS candidate pool"
            )
        ),
        # Family membership lives on the new related role, not an audited
        # owner field. Retain an empty diff so this version fence still has a
        # durable, human-readable explanation.
        allow_empty_changes=True,
    )
    return disables_auto_reject


__all__ = ["disable_owner_auto_reject_for_new_family"]
