from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...schemas.role import RoleResponse, RoleVersionCommand
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_DELETED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import assert_role_version, bump_role_version
from .job_authorization import JobPermission, require_job_permission
from .role_management_route_support import _add_role_change_boundary
from .role_support import role_to_response

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")


@router.post("/roles/{role_id}/star", response_model=RoleResponse)
def star_role(
    role_id: int,
    data: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a role as starred for auto-sync + real-time scoring.

    Side-effect: kick off an immediate Workable sync filtered to this role
    so the recruiter sees fresh candidates within seconds rather than
    waiting up to 15 min for the next Beat tick. Skipped silently for
    manual roles (no workable_job_id) or when another sync is already
    running for the org.
    """
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    audit_before = capture_role_change_snapshot(role)
    role.starred_for_auto_sync = True
    # A manual star is sticky — it must survive Workable state changes, so it
    # is never flagged auto-managed (only the published-state automation sets
    # that flag, and only it removes such stars).
    role.star_auto_managed = False
    if capture_role_change_snapshot(role) != audit_before:
        _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="role_starred",
            reason="role starred for synchronization",
            before=audit_before,
        )
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to star role")

    if (role.source == "workable") and (role.workable_job_id or "").strip():
        try:
            from ..workable_sync.routes import kick_off_filtered_sync

            org = (
                db.query(Organization)
                .filter(Organization.id == current_user.organization_id)
                .first()
            )
            if org is not None:
                kick_off_filtered_sync(
                    db,
                    org=org,
                    job_shortcodes=[str(role.workable_job_id).strip()],
                    requested_by_user_id=current_user.id,
                    mode="full",
                )
        except Exception:
            logger.exception(
                "Failed to kick off immediate sync after starring role_id=%s",
                role.id,
            )

    return role_to_response(role)


@router.delete("/roles/{role_id}/star", response_model=RoleResponse)
def unstar_role(
    role_id: int,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=expected_version)
    # Live (published) roles are always kept in continuous sync — ignore
    # attempts to unstar them. The next jobs-only sync would re-star them
    # anyway; refusing here avoids a confusing flicker and keeps the
    # invariant server-side.
    job_state = ""
    if isinstance(role.workable_job_data, dict):
        job_state = str(role.workable_job_data.get("state") or "").strip().lower()
    if job_state == "published":
        return role_to_response(role)
    audit_before = capture_role_change_snapshot(role)
    role.starred_for_auto_sync = False
    role.star_auto_managed = False
    if capture_role_change_snapshot(role) != audit_before:
        _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="role_unstarred",
            reason="role removed from synchronization favorites",
            before=audit_before,
        )
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unstar role")
    return role_to_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.DELETE_ROLE,
    )
    assert_role_version(role, expected_version=expected_version)
    has_applications = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role.id,
        )
        .first()
    )
    if has_applications:
        raise HTTPException(
            status_code=400, detail="Cannot delete role with applications"
        )
    in_use = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == current_user.organization_id,
            Assessment.role_id == role.id,
        )
        .first()
    )
    if in_use:
        raise HTTPException(
            status_code=400, detail="Cannot delete role with assessments"
        )
    audit_before = capture_role_change_snapshot(role)
    audit_from_version = int(role.version or 1)
    audit_to_version = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=audit_before,
        action=ROLE_CHANGE_ACTION_DELETED,
        actor_user_id=int(current_user.id),
        from_version=audit_from_version,
        to_version=audit_to_version,
        reason="role deleted",
        request_id=get_request_id(),
        allow_empty_changes=True,
    )
    try:
        db.delete(role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete role")
    return None
