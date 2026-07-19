"""Targeted recovery for a legacy workspace hold viewed from a related role."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import require_org_owner
from ..assessments_runtime.role_family_support import (
    role_family_response,
    roles_with_families,
)
from ...models.organization import Organization
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...schemas.role import RoleFamilyResponse
from ...services.related_role_scope_snapshot import related_role_scope_snapshot
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    ROLE_CHANGE_ACTION_AGENT_RESUMED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import assert_role_version, bump_role_version
from ...services.role_family_reject_authority import lock_current_role_families
from ...services.role_agent_dispatch import dispatch_role_agent_cycle
from ...services.workspace_agent_control import (
    WORKSPACE_BULK_PAUSE_REASON,
    advance_workspace_control,
    workspace_agent_control_snapshot,
)
from .role_control_routes import _compensate_failed_agent_dispatch


router = APIRouter()
logger = logging.getLogger("taali.agentic.related_role_recovery")


class RelatedRoleRecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    expected_workspace_control_version: int = Field(ge=1)
    expected_role_family: RoleFamilyResponse
    cohort_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    approved_max_candidates_total: int = Field(ge=0)
    approved_max_scoreable_count: int = Field(ge=0)


class RelatedRoleRecoveryScope(BaseModel):
    """Exact, one-shot authority preview for legacy related-role recovery."""

    role_id: int
    role_version: int
    workspace_paused: bool
    workspace_control_version: int
    role_family: RoleFamilyResponse
    cohort_fingerprint: str
    cohort_total: int
    cohort_scoreable: int
    cohort_unscorable: int
    cohort_excluded: int


class RelatedRoleRecoveryResult(BaseModel):
    role_id: int
    version: int
    resumed: bool
    workspace_paused: bool
    workspace_control_version: int
    preserved_paused_count: int


def _family_signature(family: RoleFamilyResponse):
    return (
        (int(family.owner.id), str(family.owner.name)),
        frozenset((int(item.id), str(item.name)) for item in family.related),
    )


def _is_current_coupled_family(role: Role, family: RoleFamilyResponse) -> bool:
    """Reject orphaned/malformed related roles before producing authority."""

    owner_id = int(role.ats_owner_role_id or 0)
    return owner_id > 0 and int(family.owner.id) == owner_id and any(
        int(item.id) == int(role.id) for item in family.related
    )


def _scope_changed(reason: str, **current) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "RELATED_ROLE_RECOVERY_SCOPE_CHANGED",
            "message": (
                "The related role, its candidate cohort, or the legacy workspace "
                "hold changed. Review the refreshed role before recovering it."
            ),
            "reason": reason,
            "current": current,
        },
    )


def _overlay_only_target_is_ready(db: Session, role: Role) -> bool:
    """Apply the same budget/readiness gates used by an explicit resume.

    Legacy workspace overlays could leave a role effectively held without a
    role-local pause.  Such a role cannot go through ``resume_if_under_budget``
    because there is no pause to clear, but it must still satisfy both gates
    before removing the overlay makes it dispatchable.
    """

    if not budget_guard.check_monthly_usd(db, role=role).ok:
        return False
    try:
        from ...services.agent_activation_readiness import activation_readiness

        return bool(activation_readiness(role).get("ready"))
    except Exception:
        logger.exception(
            "Related-role recovery readiness probe failed role_id=%s",
            role.id,
        )
        return False


@router.get(
    "/roles/{role_id}/agent/legacy-workspace-recovery-scope",
    response_model=RelatedRoleRecoveryScope,
)
def related_role_legacy_workspace_recovery_scope(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Return exact recovery authority once while the recovery control renders.

    Normal scoring progress is intentionally aggregate-only.  This owner-only
    endpoint pays the cost of loading and hashing the live CV cohort once, so
    the recruiter can authorize an exact snapshot and the mutation can still
    re-check that same fingerprint while holding its concurrency locks.
    """

    roles = roles_with_families(
        db,
        [int(role_id)],
        organization_id=int(current_user.organization_id),
    )
    role = roles.get(int(role_id))
    if role is None or role.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Role not found")
    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise HTTPException(status_code=409, detail="Role is not a coupled related role")
    family = role_family_response(role)
    if not _is_current_coupled_family(role, family):
        raise HTTPException(status_code=409, detail="Role is not a coupled related role")

    workspace_paused, workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=int(current_user.organization_id),
    )
    scope = related_role_scope_snapshot(db, role)
    return RelatedRoleRecoveryScope(
        role_id=int(role.id),
        role_version=int(role.version or 1),
        workspace_paused=bool(workspace_paused),
        workspace_control_version=int(workspace_version),
        role_family=family,
        cohort_fingerprint=str(scope["cohort_fingerprint"]),
        cohort_total=int(scope["total"]),
        cohort_scoreable=int(scope["scoreable"]),
        cohort_unscorable=int(scope["unscorable"]),
        cohort_excluded=int(scope["excluded"]),
    )


@router.post(
    "/roles/{role_id}/agent/recover-legacy-workspace-hold",
    response_model=RelatedRoleRecoveryResult,
)
def recover_related_role_legacy_workspace_hold(
    role_id: int,
    body: RelatedRoleRecoveryCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Clear a legacy overlay while resuming only the confirmed related role."""

    organization = (
        db.query(Organization)
        .filter(Organization.id == int(current_user.organization_id))
        .with_for_update(of=Organization)
        .one()
    )
    current_workspace_version = int(
        organization.agent_workspace_control_version or 1
    )
    if (
        organization.agent_workspace_paused_at is None
        or int(body.expected_workspace_control_version)
        != current_workspace_version
    ):
        raise _scope_changed(
            "workspace_hold_changed",
            workspace_paused=organization.agent_workspace_paused_at is not None,
            workspace_control_version=current_workspace_version,
        )

    families = lock_current_role_families(
        db,
        organization_id=int(current_user.organization_id),
        role_ids=[role_id],
    )
    family = families.get(int(role_id))
    role = db.get(Role, int(role_id))
    if (
        role is None
        or str(role.role_kind or "") != ROLE_KIND_SISTER
        or not role.ats_owner_role_id
        or family is None
    ):
        raise HTTPException(status_code=409, detail="Role is not a coupled related role")
    if not _is_current_coupled_family(role, family):
        raise _scope_changed(
            "role_family_changed",
            role_family=family.model_dump(),
        )
    if _family_signature(body.expected_role_family) != _family_signature(family):
        raise _scope_changed(
            "role_family_changed",
            role_family=family.model_dump(),
        )
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role={
            "id": int(role.id),
            "name": str(role.name),
            "version": int(role.version or 1),
            "role_family": family.model_dump(),
        },
    )
    scope = related_role_scope_snapshot(db, role)
    if (
        str(body.cohort_fingerprint) != str(scope["cohort_fingerprint"])
        or int(scope["total"]) > int(body.approved_max_candidates_total)
        or int(scope["scoreable"]) > int(body.approved_max_scoreable_count)
    ):
        raise _scope_changed("candidate_cohort_changed", **scope)
    if not bool(role.agentic_mode_enabled):
        raise _scope_changed("related_role_agent_off", role_version=int(role.version or 1))

    enabled_roles = (
        db.query(Role)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .all()
    )
    preserved = [item for item in enabled_roles if int(item.id) != int(role.id)]
    for item in preserved:
        if item.agent_paused_at is not None:
            continue
        before = capture_role_change_snapshot(item)
        from_version = int(item.version or 1)
        budget_guard.pause_role(db, role=item, reason=WORKSPACE_BULK_PAUSE_REASON)
        to_version = bump_role_version(item)
        add_role_change_event(
            db,
            role=item,
            before=before,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            actor_user_id=int(current_user.id),
            from_version=from_version,
            to_version=to_version,
            reason=WORKSPACE_BULK_PAUSE_REASON,
            request_id=get_request_id(),
        )

    target_version = int(role.version or 1)
    resumed = False
    if role.agent_paused_at is not None:
        # Migration 175 used this exact reason for the role-local replacement
        # of a legacy workspace hold.  Any other reason belongs to a recruiter
        # or runtime safety control and must survive overlay retirement.
        if str(role.agent_paused_reason or "") == WORKSPACE_BULK_PAUSE_REASON:
            before = capture_role_change_snapshot(role)
            from_version = target_version
            if not budget_guard.resume_if_under_budget(db, role=role, explicit=True):
                db.rollback()
                raise _scope_changed("related_role_not_ready", role_version=from_version)
            target_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
                actor_user_id=int(current_user.id),
                from_version=from_version,
                to_version=target_version,
                reason="targeted related-role recovery",
                request_id=get_request_id(),
            )
            resumed = True
    else:
        if not _overlay_only_target_is_ready(db, role):
            db.rollback()
            raise _scope_changed("related_role_not_ready", role_version=target_version)
        resumed = True
    advance_workspace_control(
        db,
        organization=organization,
        actor_user_id=int(current_user.id),
        actor_name=str(current_user.full_name or current_user.email),
        action="resumed",
        reason="targeted related-role recovery",
        request_id=get_request_id(),
    )
    db.commit()

    if resumed:
        try:
            dispatch_role_agent_cycle(role, role_version=target_version)
        except Exception as exc:
            logger.exception("Related-role recovery dispatch failed role_id=%s", role.id)
            _compensate_failed_agent_dispatch(
                db,
                role_id=int(role.id),
                dispatched_version=target_version,
                current_user=current_user,
            )
            raise HTTPException(
                status_code=503,
                detail="The worker queue is unavailable. This related role remains paused.",
            ) from exc
    return RelatedRoleRecoveryResult(
        role_id=int(role.id),
        version=target_version,
        resumed=resumed,
        workspace_paused=False,
        workspace_control_version=int(
            organization.agent_workspace_control_version or current_workspace_version + 1
        ),
        preserved_paused_count=len(preserved),
    )


__all__ = [
    "RelatedRoleRecoveryCommand",
    "RelatedRoleRecoveryResult",
    "RelatedRoleRecoveryScope",
    "related_role_legacy_workspace_recovery_scope",
    "recover_related_role_legacy_workspace_hold",
    "router",
]
