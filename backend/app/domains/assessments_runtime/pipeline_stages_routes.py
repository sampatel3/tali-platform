"""Recruiter-facing pipeline-stage management API.

CRUD over the per-org ``pipeline_stages`` table (the configurable funnel). The
behavioural effect of custom stages is gated by ``ATS_CONFIGURABLE_STAGES_ENABLED``
in pipeline_service; this management surface is always available (the frontend
shows it only when the flag is on), so flag-off prod is unaffected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user, require_role
from ...models.user import ROLE_ADMIN, ROLE_RECRUITER, User
from ...platform.database import get_db

# Pipeline configuration (stages + reason catalog) is managed by admins/recruiters.
_manage_config = require_role(ROLE_ADMIN, ROLE_RECRUITER)
from .disqualification_reasons_service import (
    create_org_reason,
    ensure_org_reasons_seeded,
    list_org_reasons,
    reorder_org_reasons,
    update_org_reason,
)
from .pipeline_stages_service import (
    create_org_stage,
    ensure_org_stages_seeded,
    list_org_stages,
    reorder_org_stages,
    update_org_stage,
)

router = APIRouter(tags=["Pipeline Stages"])


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    kind: str
    position: int
    is_default: bool
    is_active: bool


class StageCreate(BaseModel):
    name: str
    kind: str = "screening"
    slug: str | None = None
    position: int | None = None


class StageUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    position: int | None = None
    is_active: bool | None = None


class StageReorder(BaseModel):
    ordered_ids: list[int] = Field(default_factory=list)


@router.get("/pipeline/stages", response_model=list[StageOut])
def list_pipeline_stages(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    # Materialize the canonical stages on first read so the recruiter always has
    # editable rows (a fresh create_all'd DB doesn't run the migration seed).
    if ensure_org_stages_seeded(db, org_id):
        db.commit()
    return list_org_stages(db, org_id, include_inactive=include_inactive)


@router.post("/pipeline/stages", response_model=StageOut, status_code=201)
def create_pipeline_stage(
    data: StageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    row = create_org_stage(
        db,
        current_user.organization_id,
        name=data.name,
        kind=data.kind,
        slug=data.slug,
        position=data.position,
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/pipeline/stages/{stage_id}", response_model=StageOut)
def update_pipeline_stage(
    stage_id: int,
    data: StageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    row = update_org_stage(
        db,
        current_user.organization_id,
        stage_id,
        **data.model_dump(exclude_unset=True),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/pipeline/stages/reorder", response_model=list[StageOut])
def reorder_pipeline_stages(
    data: StageReorder,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    rows = reorder_org_stages(db, current_user.organization_id, data.ordered_ids)
    db.commit()
    return rows


# --- Disqualification (disposition) reasons --------------------------------


class ReasonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    category: str
    position: int
    is_default: bool
    is_active: bool


class ReasonCreate(BaseModel):
    label: str
    category: str = "we_rejected"
    position: int | None = None


class ReasonUpdate(BaseModel):
    label: str | None = None
    category: str | None = None
    position: int | None = None
    is_active: bool | None = None


class ReasonReorder(BaseModel):
    ordered_ids: list[int] = Field(default_factory=list)


@router.get(
    "/pipeline/disqualification-reasons", response_model=list[ReasonOut]
)
def list_disqualification_reasons(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    if ensure_org_reasons_seeded(db, org_id):
        db.commit()
    return list_org_reasons(db, org_id, include_inactive=include_inactive)


@router.post(
    "/pipeline/disqualification-reasons",
    response_model=ReasonOut,
    status_code=201,
)
def create_disqualification_reason(
    data: ReasonCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    row = create_org_reason(
        db,
        current_user.organization_id,
        label=data.label,
        category=data.category,
        position=data.position,
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch(
    "/pipeline/disqualification-reasons/{reason_id}", response_model=ReasonOut
)
def update_disqualification_reason(
    reason_id: int,
    data: ReasonUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    row = update_org_reason(
        db,
        current_user.organization_id,
        reason_id,
        **data.model_dump(exclude_unset=True),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post(
    "/pipeline/disqualification-reasons/reorder", response_model=list[ReasonOut]
)
def reorder_disqualification_reasons(
    data: ReasonReorder,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage_config),
):
    rows = reorder_org_reasons(db, current_user.organization_id, data.ordered_ids)
    db.commit()
    return rows
