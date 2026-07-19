"""Bind shared-roster reject confirmation to the displayed role family."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.role import ROLE_KIND_SISTER, Role
from ..schemas.role import RoleFamilyResponse, RoleReference
from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)


ROLE_FAMILY_CHANGED = "ROLE_FAMILY_CHANGED"


def lock_current_role_families(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int],
) -> dict[int, RoleFamilyResponse]:
    """Lock canonical owners, then return exact live family snapshots.

    Related-role creation takes the owner lock first. Holding that same row
    through the decision's processing transition prevents a new shared scoring
    view from appearing between the recruiter's confirmation and enqueue. The
    existing family rows are locked too so a concurrent rename cannot alter the
    human-readable confirmation boundary.
    """

    requested = sorted({int(role_id) for role_id in role_ids})
    if not requested:
        return {}
    identities = (
        db.query(Role.id, Role.role_kind, Role.ats_owner_role_id)
        .filter(
            Role.id.in_(requested),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    identity_by_id = {int(row.id): row for row in identities}
    candidate_owner_ids = {
        int(row.ats_owner_role_id)
        for row in identities
        if str(row.role_kind or "") == ROLE_KIND_SISTER
        and row.ats_owner_role_id is not None
    }
    live_owner_ids = {
        int(role_id)
        for (role_id,) in db.query(Role.id)
        .filter(
            Role.id.in_(candidate_owner_ids),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .all()
    }
    owner_id_by_role: dict[int, int] = {}
    for role_id, row in identity_by_id.items():
        candidate = (
            int(row.ats_owner_role_id)
            if str(row.role_kind or "") == ROLE_KIND_SISTER
            and row.ats_owner_role_id is not None
            else role_id
        )
        owner_id_by_role[role_id] = (
            candidate if candidate in live_owner_ids else role_id
        )

    owner_ids = sorted(set(owner_id_by_role.values()))
    # Lock owners in a stable order before any family member or decision row.
    owners = (
        db.query(Role)
        .filter(
            Role.id.in_(owner_ids),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.id.asc())
        .populate_existing()
        .with_for_update(of=Role)
        .all()
    )
    owners_by_id = {int(owner.id): owner for owner in owners}
    family_rows = (
        db.query(Role)
        .filter(
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
            or_(Role.id.in_(owner_ids), Role.ats_owner_role_id.in_(owner_ids)),
        )
        .order_by(Role.id.asc())
        .populate_existing()
        .with_for_update(of=Role)
        .all()
    )
    related_by_owner: dict[int, list[Role]] = {owner_id: [] for owner_id in owner_ids}
    for row in family_rows:
        owner_id = getattr(row, "ats_owner_role_id", None)
        if owner_id is not None and int(owner_id) in related_by_owner:
            related_by_owner[int(owner_id)].append(row)

    family_by_owner: dict[int, RoleFamilyResponse] = {}
    for owner_id, owner in owners_by_id.items():
        related = sorted(
            related_by_owner.get(owner_id, []),
            key=lambda row: (str(row.name or "").casefold(), int(row.id)),
        )
        family_by_owner[owner_id] = RoleFamilyResponse(
            owner=RoleReference(id=owner_id, name=str(owner.name)),
            related=[
                RoleReference(id=int(row.id), name=str(row.name)) for row in related
            ],
        )
    return {
        role_id: family_by_owner[owner_id]
        for role_id, owner_id in owner_id_by_role.items()
        if owner_id in family_by_owner
    }


def require_expected_role_family(
    *,
    expected: RoleFamilyResponse | None,
    current: RoleFamilyResponse,
) -> None:
    """Reject a stale/absent family proof only for shared-role families."""

    if not current.related:
        return

    def signature(family: RoleFamilyResponse | dict | None):
        if family is None:
            return None
        if isinstance(family, dict):
            family = RoleFamilyResponse.model_validate(family)
        return (
            (int(family.owner.id), str(family.owner.name)),
            frozenset((int(role.id), str(role.name)) for role in family.related),
        )

    if signature(expected) == signature(current):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": ROLE_FAMILY_CHANGED,
            "message": (
                "The shared role family changed after this reject was shown. "
                "Review the current linked roles before confirming again."
            ),
            "current_role_family": current.model_dump(),
        },
    )


def authorize_single_decision_action(
    db: Session,
    *,
    current_user: Any,
    role_id: int,
    reject: bool,
    expected: RoleFamilyResponse | None,
) -> Role:
    """Authorize one decision, binding reject to its canonical family."""

    families = (
        lock_current_role_families(
            db,
            organization_id=int(current_user.organization_id),
            role_ids=[int(role_id)],
        )
        if reject
        else {}
    )
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    current = families.get(int(role_id))
    if current is not None:
        require_expected_role_family(expected=expected, current=current)
    return role


def authorize_bulk_decision_actions(
    db: Session,
    *,
    current_user: Any,
    decisions: Iterable[Any],
    reject_action: Literal["approve", "override", "none"],
    expected_families: dict[str, RoleFamilyResponse] | None,
) -> None:
    """Authorize a decision batch and validate every actionable reject family."""

    rows = list(decisions)
    role_ids = {
        int(row.role_id) for row in rows if getattr(row, "role_id", None) is not None
    }
    reject_role_ids = {
        int(row.role_id)
        for row in rows
        if getattr(row, "role_id", None) is not None
        and (
            (
                reject_action == "approve"
                and row.status == "pending"
                and str(row.decision_type) in ("reject", "skip_assessment_reject")
            )
            or (
                reject_action == "override"
                and row.status in ("pending", "reverted_for_feedback")
            )
        )
    }
    current_families = lock_current_role_families(
        db,
        organization_id=int(current_user.organization_id),
        role_ids=reject_role_ids,
    )
    for role_id in sorted(role_ids):
        require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
        )
    expected_by_role = expected_families or {}
    for role_id, current in current_families.items():
        require_expected_role_family(
            expected=expected_by_role.get(str(role_id)),
            current=current,
        )


__all__ = [
    "ROLE_FAMILY_CHANGED",
    "authorize_bulk_decision_actions",
    "authorize_single_decision_action",
    "lock_current_role_families",
    "require_expected_role_family",
]
