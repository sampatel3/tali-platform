"""Coupled Taali sister-role creation and scoring lifecycle."""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from ...deps import get_current_user
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SISTER_EVAL_ERROR,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from ...models.user import User
from ...platform.database import get_db
from ...schemas.sister_role import (
    SisterRoleCreate,
    SisterRoleCreateResponse,
    SisterRolePreview,
    SisterRoleScoringStatus,
)
from ...services.related_role_service import (
    RelatedRoleError,
    create_related_role as create_related_role_record,
    get_related_role_source,
    related_role_roster_counts,
)
from ...services.ats_role_lifecycle import ats_job_lifecycle
from ...services.sister_role_service import ensure_sister_evaluations
from ...tasks.sister_role_tasks import score_sister_role
from .roles_management_routes import _serialize_role_detail

router = APIRouter(tags=["Sister roles"])
logger = logging.getLogger("taali.sister_roles")


def _source_role(db: Session, *, role_id: int, organization_id: int) -> Role:
    try:
        return get_related_role_source(
            db, role_id=role_id, organization_id=organization_id
        )
    except RelatedRoleError as exc:
        code = 404 if str(exc) == "Role not found." else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


def _sister_role(db: Session, *, role_id: int, organization_id: int) -> Role:
    role = (
        db.query(Role)
        .options(joinedload(Role.ats_owner_role), selectinload(Role.sister_roles))
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise HTTPException(status_code=409, detail="Role is not a coupled related role")
    return role


def _roster_counts(db: Session, source: Role) -> dict[str, int]:
    return related_role_roster_counts(db, source)


@router.get("/roles/{source_role_id}/sisters/preview", response_model=SisterRolePreview)
def preview_sister_role(
    source_role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = _source_role(
        db, role_id=source_role_id, organization_id=current_user.organization_id
    )
    counts = _roster_counts(db, source)
    return SisterRolePreview(
        source_role_id=source.id,
        source_role_name=source.name,
        source_ats_provider=ats_job_lifecycle(source).provider or "",
        candidates_total=counts["total"],
        candidates_with_cv=counts["with_cv"],
        candidates_missing_cv=counts["missing_cv"],
    )


@router.post(
    "/roles/{source_role_id}/sisters",
    response_model=SisterRoleCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sister_role(
    source_role_id: int,
    data: SisterRoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = _source_role(
        db, role_id=source_role_id, organization_id=current_user.organization_id
    )
    try:
        sister, evaluation_counts = create_related_role_record(
            db,
            role_id=int(source.id),
            organization_id=int(current_user.organization_id),
            name=data.name,
            job_spec_text=data.job_spec_text,
        )
    except RelatedRoleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    loaded = (
        db.query(Role)
        .options(
            joinedload(Role.tasks),
            joinedload(Role.ats_owner_role),
            selectinload(Role.sister_roles),
        )
        .filter(Role.id == sister.id)
        .first()
    )
    return SisterRoleCreateResponse(
        role=_serialize_role_detail(db, loaded or sister, current_user.organization_id),
        evaluation_counts=evaluation_counts,
    )


@router.post("/roles/{role_id}/sister-rescore", response_model=SisterRoleScoringStatus)
def rescore_sister_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _sister_role(db, role_id=role_id, organization_id=current_user.organization_id)
    ensure_sister_evaluations(db, role, reset_existing=True)
    db.commit()
    try:
        score_sister_role.apply_async(args=[role.id], queue="scoring")
    except Exception as exc:  # Beat recovers pending rows without user action.
        logger.error(
            "Related-role rescore kick unavailable role_id=%s error_code=queue_unavailable error_type=%s",
            role.id,
            type(exc).__name__,
        )
    return _scoring_status(db, role)


def _scoring_status(db: Session, role: Role) -> SisterRoleScoringStatus:
    rows = (
        db.query(SisterRoleEvaluation.status, func.count(SisterRoleEvaluation.id))
        .filter(SisterRoleEvaluation.role_id == role.id)
        .group_by(SisterRoleEvaluation.status)
        .all()
    )
    counts = Counter({str(key): int(value) for key, value in rows})
    for key in (
        SISTER_EVAL_PENDING, SISTER_EVAL_RUNNING, SISTER_EVAL_RETRY_WAIT, SISTER_EVAL_DONE,
        SISTER_EVAL_ERROR, SISTER_EVAL_UNSCORABLE,
    ):
        counts.setdefault(key, 0)
    total = sum(counts.values())
    completed = counts[SISTER_EVAL_DONE] + counts[SISTER_EVAL_ERROR] + counts[SISTER_EVAL_UNSCORABLE]
    if (
        counts[SISTER_EVAL_RUNNING]
        or counts[SISTER_EVAL_PENDING]
        or counts[SISTER_EVAL_RETRY_WAIT]
    ):
        overall = "running"
    elif counts[SISTER_EVAL_ERROR] and not counts[SISTER_EVAL_DONE]:
        overall = "error"
    else:
        overall = "completed"
    top = (
        db.query(
            SisterRoleEvaluation.source_application_id,
            SisterRoleEvaluation.role_fit_score,
            Candidate.full_name,
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            SisterRoleEvaluation.role_id == role.id,
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
        )
        .order_by(SisterRoleEvaluation.role_fit_score.desc().nullslast())
        .limit(5)
        .all()
    )
    return SisterRoleScoringStatus(
        role_id=role.id,
        status=overall,
        counts=dict(counts),
        total=total,
        completed=completed,
        progress_percent=round((completed / total * 100.0) if total else 100.0, 1),
        top_candidates=[
            {"application_id": app_id, "candidate_name": name, "score": score}
            for app_id, score, name in top
        ],
    )


@router.get("/roles/{role_id}/sister-scoring-status", response_model=SisterRoleScoringStatus)
def sister_role_scoring_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _sister_role(db, role_id=role_id, organization_id=current_user.organization_id)
    return _scoring_status(db, role)
