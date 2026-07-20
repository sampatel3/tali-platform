"""Transaction boundary for role-criterion mutations."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.role import Role
from ...models.user import User
from ...platform.request_context import get_request_id
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version
from ...services.role_provider_artifact_lifecycle import (
    invalidate_role_provider_artifacts_if_changed,
)
from ...services.role_provider_generation import (
    RoleProviderGeneration,
    capture_role_provider_generation,
)


def capture_criterion_provider_generation(
    db: Session,
    role: Role,
) -> RoleProviderGeneration | None:
    """Capture provider inputs before a criterion mutation is flushed."""

    return capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )


def commit_role_criterion_change(
    db: Session,
    role: Role,
    *,
    current_user: User,
    previous_provider_generation: RoleProviderGeneration | None,
    invalidate_scores: bool = True,
) -> None:
    """Atomically invalidate affected outputs, audit, and commit a chip edit."""

    provider_inputs_changed = invalidate_role_provider_artifacts_if_changed(
        db,
        role=role,
        previous=previous_provider_generation,
    )
    if invalidate_scores and provider_inputs_changed:
        mark_role_scores_stale(
            db,
            role.id,
        )

    audit_before = capture_role_change_snapshot(role)
    from_version = int(role.version or 1)
    to_version = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=audit_before,
        action="role_criteria_updated",
        actor_user_id=int(current_user.id),
        from_version=from_version,
        to_version=to_version,
        reason="job criteria updated",
        request_id=get_request_id(),
        allow_empty_changes=True,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role criteria")


__all__ = [
    "capture_criterion_provider_generation",
    "commit_role_criterion_change",
]
