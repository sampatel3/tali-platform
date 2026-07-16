from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.org_criterion import BUCKET_PREFERRED, CRITERION_BUCKETS
from ...models.role import Role
from ...models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
    RoleCriterion,
)
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import (
    RoleCriterionCreate,
    RoleCriterionResponse,
    RoleCriterionUpdate,
    RoleResponse,
    RoleVersionCommand,
)
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.role_concurrency import assert_role_version
from ...services.role_criteria_service import (
    reset_role_to_workspace,
    sync_role_with_workspace,
)
from .job_authorization import JobPermission, require_job_permission
from .role_management_route_support import _add_role_change_boundary
from .role_support import role_to_response

router = APIRouter(tags=["Roles"])

# ---------------------------------------------------------------------------
# Per-role criteria — chip CRUD, sync, reset
# ---------------------------------------------------------------------------


def _get_role_criterion(db: Session, role: Role, criterion_id: int) -> RoleCriterion:
    chip = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.id == criterion_id,
            RoleCriterion.role_id == role.id,
            RoleCriterion.deleted_at.is_(None),
            RoleCriterion.source != CRITERION_SOURCE_DERIVED,
        )
        .first()
    )
    if chip is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    return chip


def _next_role_criterion_ordering(db: Session, role: Role) -> int:
    last = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.deleted_at.is_(None),
            RoleCriterion.source != CRITERION_SOURCE_DERIVED,
        )
        .order_by(RoleCriterion.ordering.desc(), RoleCriterion.id.desc())
        .first()
    )
    return (last.ordering + 1) if last else 0


# Pre-screen only reads must-have + constraint criteria — it explicitly
# ignores nice-to-haves. So preferred-only edits don't change the
# pre-screen prompt and shouldn't invalidate any candidate's score.
# Edits that touch must-have OR constraint (either side of the
# transition) DO change the pre-screen prompt and need an invalidation
# wave.
_INVALIDATING_BUCKETS = {"must", "constraint"}


def _commit_role_criterion_change(
    db: Session,
    role: Role,
    *,
    current_user: User,
    invalidate_scores: bool = True,
) -> None:
    """Commit a chip CRUD. Optionally NULLs every scored application's
    pre-screen + cv_match scores so the UI shows "needs rescore" until
    the agent re-evaluates against the new criteria.

    ``invalidate_scores`` defaults to ``True`` (the historical, safe
    behavior — invalidate on any change). Per-chip CRUD handlers
    (create / update / delete) pass an explicit value computed from
    the bucket transition; bulk workspace re-sync / reset handlers
    pass nothing and get the safe default.
    """
    db.flush()
    if invalidate_scores:
        mark_role_scores_stale(db, role.id)
    _add_role_change_boundary(
        db,
        role=role,
        current_user=current_user,
        action="role_criteria_updated",
        reason="job criteria updated",
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role criteria")


@router.post(
    "/roles/{role_id}/criteria",
    response_model=RoleCriterionResponse,
    status_code=201,
)
def create_role_criterion(
    role_id: int,
    data: RoleCriterionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    bucket = data.bucket or BUCKET_PREFERRED
    if bucket not in CRITERION_BUCKETS:
        raise HTTPException(status_code=422, detail="Invalid bucket")
    chip = RoleCriterion(
        role_id=role.id,
        source=CRITERION_SOURCE_RECRUITER,
        ordering=int(data.ordering)
        if data.ordering is not None
        else _next_role_criterion_ordering(db, role),
        weight=float(data.weight) if data.weight is not None else 1.0,
        must_have=(bucket == "must"),
        bucket=bucket,
        org_criterion_id=None,
        text=data.text.strip(),
    )
    db.add(chip)
    _commit_role_criterion_change(
        db,
        role,
        current_user=current_user,
        invalidate_scores=bucket in _INVALIDATING_BUCKETS,
    )
    db.refresh(chip)
    return RoleCriterionResponse.model_validate(chip).model_copy(
        update={"role_version": int(role.version or 1)}
    )


@router.patch(
    "/roles/{role_id}/criteria/{criterion_id}",
    response_model=RoleCriterionResponse,
)
def update_role_criterion(
    role_id: int,
    criterion_id: int,
    data: RoleCriterionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    chip = _get_role_criterion(db, role, criterion_id)
    updates = data.model_dump(exclude_unset=True)
    updates.pop("expected_version", None)
    old_bucket = chip.bucket
    text_changed = (
        "text" in updates
        and updates["text"] is not None
        and updates["text"].strip() != (chip.text or "")
    )
    bucket_changed = (
        "bucket" in updates
        and updates["bucket"] is not None
        and updates["bucket"] != chip.bucket
    )
    if "text" in updates and updates["text"] is not None:
        chip.text = updates["text"].strip()
    if "bucket" in updates and updates["bucket"] is not None:
        if updates["bucket"] not in CRITERION_BUCKETS:
            raise HTTPException(status_code=422, detail="Invalid bucket")
        chip.bucket = updates["bucket"]
        chip.must_have = chip.bucket == "must"
    if "ordering" in updates and updates["ordering"] is not None:
        chip.ordering = int(updates["ordering"])
    if "weight" in updates and updates["weight"] is not None:
        chip.weight = float(updates["weight"])
    # Mark customized so a later "Sync workspace" doesn't overwrite recruiter
    # edits to a workspace-derived chip. Pure ordering/weight tweaks don't
    # count as content customization.
    if (text_changed or bucket_changed) and chip.org_criterion_id is not None:
        chip.customized_at = datetime.now(timezone.utc)
    # Invalidate scores if the edit could have changed the pre-screen
    # prompt: text/bucket edits where either the old OR new bucket is
    # must-have/constraint. Pure ordering/weight tweaks, and pure
    # preferred→preferred text edits, don't trigger.
    needs_invalidation = (text_changed or bucket_changed) and (
        old_bucket in _INVALIDATING_BUCKETS or chip.bucket in _INVALIDATING_BUCKETS
    )
    _commit_role_criterion_change(
        db,
        role,
        current_user=current_user,
        invalidate_scores=needs_invalidation,
    )
    db.refresh(chip)
    return RoleCriterionResponse.model_validate(chip).model_copy(
        update={"role_version": int(role.version or 1)}
    )


@router.delete(
    "/roles/{role_id}/criteria/{criterion_id}",
    status_code=204,
)
def delete_role_criterion(
    role_id: int,
    criterion_id: int,
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
    chip = _get_role_criterion(db, role, criterion_id)
    old_bucket = chip.bucket
    # If this chip was inherited from workspace, remember the suppression so
    # "Sync workspace" doesn't immediately re-add it. Pure role-only chips
    # just go away.
    if chip.org_criterion_id is not None:
        suppressed = list(role.suppressed_org_criterion_ids or [])
        if chip.org_criterion_id not in suppressed:
            suppressed.append(int(chip.org_criterion_id))
        role.suppressed_org_criterion_ids = suppressed
    db.delete(chip)
    _commit_role_criterion_change(
        db,
        role,
        current_user=current_user,
        invalidate_scores=old_bucket in _INVALIDATING_BUCKETS,
    )
    return None


@router.post("/roles/{role_id}/criteria/sync", response_model=RoleResponse)
def sync_role_criteria_with_workspace(
    role_id: int,
    data: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-apply workspace text + bucket on non-customized, non-suppressed
    role chips, add any newly-introduced workspace chips, drop the
    workspace link on chips whose workspace counterpart is gone."""
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    sync_role_with_workspace(db, role)
    _commit_role_criterion_change(db, role, current_user=current_user)
    db.refresh(role)
    return role_to_response(role)


@router.post("/roles/{role_id}/criteria/reset", response_model=RoleResponse)
def reset_role_criteria_to_workspace(
    role_id: int,
    data: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hard-delete every recruiter chip on this role and re-snapshot
    workspace defaults. Suppressions are cleared. ``derived_from_spec``
    chips are untouched."""
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    reset_role_to_workspace(db, role)
    _commit_role_criterion_change(db, role, current_user=current_user)
    db.refresh(role)
    return role_to_response(role)
