"""Workspace criteria CRUD — Settings → AI agent chip composer.

Lives in its own router so the parent ``organization_routes`` stays
under the architecture file-size guard. Mounted under the same
``/organizations`` prefix as ``organization_routes`` (see
``main.py``) so the URL surface is unchanged
(``GET/POST/PATCH/DELETE /organizations/me/criteria[/{id}]``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.org_criterion import (
    BUCKET_PREFERRED,
    CRITERION_BUCKETS,
    OrganizationCriterion,
)
from ...models.organization import Organization
from ...models.user import User
from ...platform.database import get_db
from ...schemas.organization import (
    OrgCriterionCreate,
    OrgCriterionResponse,
    OrgCriterionUpdate,
)


router = APIRouter(prefix="/organizations", tags=["Organizations"])


def _active_org_criteria_query(db: Session, organization_id: int):
    return (
        db.query(OrganizationCriterion)
        .filter(
            OrganizationCriterion.organization_id == organization_id,
            OrganizationCriterion.deleted_at.is_(None),
        )
        .order_by(OrganizationCriterion.ordering, OrganizationCriterion.id)
    )


def _next_ordering(db: Session, organization_id: int) -> int:
    last = (
        _active_org_criteria_query(db, organization_id)
        .order_by(OrganizationCriterion.ordering.desc(), OrganizationCriterion.id.desc())
        .first()
    )
    return (last.ordering + 1) if last else 0


@router.get("/me/criteria", response_model=list[OrgCriterionResponse])
def list_org_criteria(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _active_org_criteria_query(db, current_user.organization_id).all()


@router.post(
    "/me/criteria",
    response_model=OrgCriterionResponse,
    status_code=201,
)
def create_org_criterion(
    data: OrgCriterionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    ordering = (
        data.ordering
        if data.ordering is not None
        else _next_ordering(db, org.id)
    )
    chip = OrganizationCriterion(
        organization_id=org.id,
        ordering=int(ordering),
        weight=float(data.weight) if data.weight is not None else 1.0,
        bucket=data.bucket or BUCKET_PREFERRED,
        text=data.text.strip(),
    )
    db.add(chip)
    try:
        db.commit()
        db.refresh(chip)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create criterion")
    return chip


@router.patch("/me/criteria/{criterion_id}", response_model=OrgCriterionResponse)
def update_org_criterion(
    criterion_id: int,
    data: OrgCriterionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    chip = (
        db.query(OrganizationCriterion)
        .filter(
            OrganizationCriterion.id == criterion_id,
            OrganizationCriterion.organization_id == current_user.organization_id,
            OrganizationCriterion.deleted_at.is_(None),
        )
        .first()
    )
    if chip is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    updates = data.model_dump(exclude_unset=True)
    if "text" in updates and updates["text"] is not None:
        chip.text = updates["text"].strip()
    if "bucket" in updates and updates["bucket"] is not None:
        if updates["bucket"] not in CRITERION_BUCKETS:
            raise HTTPException(status_code=422, detail="Invalid bucket")
        chip.bucket = updates["bucket"]
    if "ordering" in updates and updates["ordering"] is not None:
        chip.ordering = int(updates["ordering"])
    if "weight" in updates and updates["weight"] is not None:
        chip.weight = float(updates["weight"])
    try:
        db.commit()
        db.refresh(chip)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update criterion")
    return chip


@router.delete("/me/criteria/{criterion_id}", status_code=204)
def delete_org_criterion(
    criterion_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    chip = (
        db.query(OrganizationCriterion)
        .filter(
            OrganizationCriterion.id == criterion_id,
            OrganizationCriterion.organization_id == current_user.organization_id,
            OrganizationCriterion.deleted_at.is_(None),
        )
        .first()
    )
    if chip is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    chip.deleted_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete criterion")
    return None
