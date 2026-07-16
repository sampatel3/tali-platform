"""Authorization boundary for recruiter-owned outreach campaign mutations.

Role-linked campaigns inherit the role's hiring-team policy.  Role-less
campaigns have no hiring team to consult, so they remain private for mutation
purposes: only their original creator or an organization owner may change or
dispatch them.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.outreach_campaign import OutreachCampaign
from ...models.role import Role
from ...models.user import User
from ..assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def require_role_editor(db: Session, role_id: int, current_user: User) -> Role:
    """Authorize creation of a role-linked campaign while preserving 404 scope."""
    organization_id = getattr(current_user, "organization_id", None)
    visible_role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if visible_role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=True,
    )


def _require_campaign_permission(
    db: Session,
    campaign_id: int,
    current_user: User,
    permission: JobPermission,
) -> OutreachCampaign:
    """Return an org-scoped campaign after its authorization policy passes.

    Role-linked mutations must take the canonical Role -> child lock order used
    by every other shared-job write.  The first campaign read is deliberately
    unlocked: it discovers the tenant-scoped role id, then the role is
    authorized/locked before the campaign row is locked and revalidated.
    """
    organization_id = getattr(current_user, "organization_id", None)
    query = db.query(OutreachCampaign).filter(
        OutreachCampaign.id == campaign_id,
        OutreachCampaign.organization_id == organization_id,
    )
    campaign = query.first()
    if campaign is None:
        # Existing campaign endpoints deliberately conceal cross-org ids.
        raise HTTPException(status_code=404, detail="Campaign not found")

    if permission is JobPermission.VIEW:
        return _authorize_campaign(db, campaign, current_user, permission)

    initial_role_id = (
        int(campaign.role_id) if campaign.role_id is not None else None
    )
    if initial_role_id is not None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=initial_role_id,
            permission=permission,
            lock_for_update=True,
        )
        locked_campaign = (
            query.filter(OutreachCampaign.role_id == initial_role_id)
            .with_for_update(of=OutreachCampaign)
            .populate_existing()
            .first()
        )
        if locked_campaign is None:
            raise HTTPException(
                status_code=409,
                detail="The campaign's linked job changed; refresh and retry.",
            )
        return locked_campaign

    # A role-less campaign has no parent Role lock. Lock it directly, while
    # refusing/retrying if another workflow linked it after the discovery read.
    locked_campaign = (
        query.filter(OutreachCampaign.role_id.is_(None))
        .with_for_update(of=OutreachCampaign)
        .populate_existing()
        .first()
    )
    if locked_campaign is None:
        raise HTTPException(
            status_code=409,
            detail="The campaign's linked job changed; refresh and retry.",
        )
    return _authorize_campaign(db, locked_campaign, current_user, permission)


def _authorize_campaign(
    db: Session,
    campaign: OutreachCampaign,
    current_user: User,
    permission: JobPermission,
) -> OutreachCampaign:
    if campaign.role_id is not None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(campaign.role_id),
            permission=permission,
            lock_for_update=permission is not JobPermission.VIEW,
        )
        return campaign

    if not bool(getattr(current_user, "is_active", False)):
        raise _forbidden()
    if getattr(current_user, "role", None) == "owner":
        return campaign
    if campaign.created_by_user_id is not None and int(
        campaign.created_by_user_id
    ) == int(current_user.id):
        return campaign
    raise _forbidden()


def require_campaign_viewer(
    db: Session, campaign_id: int, current_user: User
) -> OutreachCampaign:
    """Require the job VIEW policy or role-less campaign ownership."""
    return _require_campaign_permission(
        db, campaign_id, current_user, JobPermission.VIEW
    )


def filter_viewable_campaigns(
    db: Session,
    campaigns: Iterable[OutreachCampaign],
    current_user: User,
) -> list[OutreachCampaign]:
    """Drop campaigns the caller cannot view without leaking their existence."""
    visible: list[OutreachCampaign] = []
    for campaign in campaigns:
        try:
            visible.append(
                _authorize_campaign(db, campaign, current_user, JobPermission.VIEW)
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_403_FORBIDDEN:
                raise
    return visible


def require_campaign_editor(
    db: Session, campaign_id: int, current_user: User
) -> OutreachCampaign:
    """Require role-content authority (or role-less campaign ownership)."""
    return _require_campaign_permission(
        db, campaign_id, current_user, JobPermission.EDIT_ROLE
    )


def require_campaign_controller(
    db: Session, campaign_id: int, current_user: User
) -> OutreachCampaign:
    """Require automation authority (or role-less campaign ownership)."""
    return _require_campaign_permission(
        db, campaign_id, current_user, JobPermission.CONTROL_AGENT
    )


__all__ = [
    "filter_viewable_campaigns",
    "require_campaign_controller",
    "require_campaign_editor",
    "require_campaign_viewer",
    "require_role_editor",
]
