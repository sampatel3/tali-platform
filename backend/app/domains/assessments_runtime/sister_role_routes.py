"""Independent Taali related-role creation and scoring lifecycle."""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SISTER_EVAL_ERROR,
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_STALE_HELD,
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
from ...schemas.role import ApplicationResponse, ApplicationStageUpdate
from ...services.related_role_service import (
    RelatedRoleError,
    create_related_role as create_related_role_record,
    get_related_role_source,
    related_role_roster_counts,
)
from ...services.ats_role_lifecycle import ats_job_lifecycle
from ...services.job_page_lifecycle import role_paid_ats_work_block_reason
from ...services.sister_role_service import (
    ensure_sister_evaluations,
    project_sister_application,
    related_role_ats_owner,
)
from ...tasks.sister_role_tasks import score_sister_role
from .roles_management_routes import _serialize_role_detail
from .job_authorization import JobPermission, require_job_permission
from .related_role_actions import move_related_role_application_stage
from .role_support import role_family_load_options

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
        .options(*role_family_load_options(organization_id=organization_id))
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if str(role.role_kind or "") != ROLE_KIND_SISTER:
        raise HTTPException(status_code=409, detail="Role is not a related role")
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
        source_ats_provider=(
            ats_job_lifecycle(related_role_ats_owner(db, source)).provider
        ),
        candidates_total=counts["total"],
        candidates_with_cv=counts["with_cv"],
        candidates_missing_cv=counts["missing_cv"],
        source_snapshot_fingerprint=counts["snapshot_fingerprint"],
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
    require_job_permission(
        db,
        current_user=current_user,
        role_id=source_role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    source = _source_role(
        db, role_id=source_role_id, organization_id=current_user.organization_id
    )
    try:
        sister, evaluation_counts = create_related_role_record(
            db,
            role_id=int(source.id),
            organization_id=int(current_user.organization_id),
            creator_user_id=int(current_user.id),
            name=data.name,
            job_spec_text=data.job_spec_text,
            expected_source_snapshot_fingerprint=(
                data.source_snapshot_fingerprint
            ),
        )
    except RelatedRoleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    loaded = (
        db.query(Role)
        .options(
            joinedload(Role.tasks),
            *role_family_load_options(
                organization_id=int(current_user.organization_id)
            ),
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
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
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
        .filter(
            SisterRoleEvaluation.role_id == role.id,
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .group_by(SisterRoleEvaluation.status)
        .all()
    )
    counts = Counter({str(key): int(value) for key, value in rows})
    for key in (
        SISTER_EVAL_PENDING, SISTER_EVAL_RUNNING, SISTER_EVAL_RETRY_WAIT, SISTER_EVAL_DONE,
        SISTER_EVAL_ERROR, SISTER_EVAL_UNSCORABLE, SISTER_EVAL_EXCLUDED,
        SISTER_EVAL_STALE_HELD,
    ):
        counts.setdefault(key, 0)
    total = sum(counts.values())
    completed = (
        counts[SISTER_EVAL_DONE]
        + counts[SISTER_EVAL_ERROR]
        + counts[SISTER_EVAL_UNSCORABLE]
        + counts[SISTER_EVAL_EXCLUDED]
    )
    scoreable_total = max(
        total - counts[SISTER_EVAL_UNSCORABLE] - counts[SISTER_EVAL_EXCLUDED], 0
    )
    scoreable_completed = counts[SISTER_EVAL_DONE] + counts[SISTER_EVAL_ERROR]
    authority_waiting = int(
        db.query(func.count(SisterRoleEvaluation.id))
        .filter(
            SisterRoleEvaluation.role_id == role.id,
            SisterRoleEvaluation.deleted_at.is_(None),
            SisterRoleEvaluation.status == SISTER_EVAL_RETRY_WAIT,
            SisterRoleEvaluation.last_error_code == "authority_blocked",
        )
        .scalar()
        or 0
    )
    waiting_reason = None
    if counts[SISTER_EVAL_STALE_HELD]:
        waiting_reason = "recruiter_re_evaluate_required"
    elif authority_waiting:
        waiting_reason = (
            role_paid_ats_work_block_reason(role, db=db)
            or "authority_blocked"
        )
    elif counts[SISTER_EVAL_RETRY_WAIT]:
        waiting_reason = "temporary_retry"
    if (
        counts[SISTER_EVAL_RUNNING]
        or counts[SISTER_EVAL_PENDING]
    ):
        overall = "running"
    elif counts[SISTER_EVAL_RETRY_WAIT] or counts[SISTER_EVAL_STALE_HELD]:
        overall = "waiting"
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
            SisterRoleEvaluation.deleted_at.is_(None),
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
        scoreable_total=scoreable_total,
        scored=counts[SISTER_EVAL_DONE],
        completed=completed,
        progress_percent=round(
            (scoreable_completed / scoreable_total * 100.0)
            if scoreable_total
            else 100.0,
            1,
        ),
        waiting_reason=waiting_reason,
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


@router.patch(
    "/roles/{role_id}/applications/{application_id}/stage",
    response_model=ApplicationResponse,
)
def update_related_role_application_stage(
    role_id: int,
    application_id: int,
    data: ApplicationStageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move one candidate inside this related role's Taali funnel."""

    from .applications_routes import application_to_response

    # Explicit membership survives evidence-row soft deletion; membership and
    # role authorization below are the authority for this local mutation.
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(current_user.organization_id),
        )
        .one_or_none()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    role, evaluation = move_related_role_application_stage(
        db,
        current_user=current_user,
        related_role_id=role_id,
        application=app,
        to_stage=data.pipeline_stage,
        expected_version=data.expected_version,
        idempotency_key=data.idempotency_key,
    )
    payload = application_to_response(
        app, use_cached_score_summary=True
    ).model_dump(mode="python")
    owner_role = (
        db.get(Role, int(role.ats_owner_role_id))
        if role.ats_owner_role_id is not None
        else None
    )
    return ApplicationResponse(
        **project_sister_application(
            payload,
            sister_role=role,
            owner_role=owner_role,
            evaluation=evaluation,
            db=db,
            application=app,
        )
    )
