"""Coupled Taali sister-role creation and scoring lifecycle."""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func
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
    SISTER_EVAL_STALE,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from ...models.user import User
from ...platform.database import get_db
from ...schemas.sister_role import (
    SisterRoleCreate,
    SisterRoleCreateResponse,
    SisterRolePreview,
    SisterRoleRescoreRequest,
    SisterRoleScoringStatus,
)
from ...schemas.role import ApplicationResponse, ApplicationStageUpdate
from ...services.related_role_service import (
    RelatedRoleError,
    create_related_role as create_related_role_record,
    get_related_role_source,
    preview_related_role,
)
from ...services.job_page_lifecycle import role_paid_ats_work_block_reason
from ...services import related_role_pipeline_queries as related_pipeline
from ...services.sister_role_service import ensure_sister_evaluations, project_sister_application
from ...services.related_role_paid_work_authorization import (
    require_related_role_publish_authority,
    require_related_role_rescore_scope,
)
from ...services.related_role_spec_lifecycle import RELATED_ROLE_SCORE_COST_USD
from ...services.related_role_scope_snapshot import related_role_scope_counts
from ...services.role_concurrency import assert_role_version
from ...tasks.sister_role_tasks import score_sister_role
from .roles_management_routes import _serialize_role_detail
from .job_authorization import JobPermission, require_job_permission
from .related_role_actions import move_related_role_application_stage
from .role_support import role_family_load_options

router = APIRouter(tags=["Sister roles"])
logger = logging.getLogger("taali.sister_roles")


def _source_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    lock_for_update: bool = False,
) -> Role:
    try:
        return get_related_role_source(
            db,
            role_id=role_id,
            organization_id=organization_id,
            lock_for_update=lock_for_update,
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
    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise HTTPException(status_code=409, detail="Role is not a coupled related role")
    return role


@router.get("/roles/{source_role_id}/sisters/preview", response_model=SisterRolePreview)
def preview_sister_role(
    source_role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return SisterRolePreview.model_validate(
            preview_related_role(
                db,
                role_id=source_role_id,
                organization_id=int(current_user.organization_id),
            )
        )
    except RelatedRoleError as exc:
        code = 404 if str(exc) == "Role not found." else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


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
    preview = preview_related_role(
        db,
        role_id=int(source.id),
        organization_id=int(current_user.organization_id),
    )
    try:
        require_related_role_publish_authority(
            authority=data.related_role_authorization,
            source_role=source,
            candidates_total=int(preview["candidates_total"]),
            scoreable_count=int(preview["candidates_scoreable"]),
            current_default_monthly_budget_cents=int(
                preview["proposed_monthly_budget_cents"]
            ),
        )
    except HTTPException:
        db.rollback()
        raise

    def authorize_created_scope(related: Role, counts: dict[str, int]) -> None:
        post = preview_related_role(
            db,
            role_id=int(source.id),
            organization_id=int(current_user.organization_id),
        )
        require_related_role_publish_authority(
            authority=data.related_role_authorization,
            source_role=source,
            related_role=related,
            candidates_total=int(counts.get("total") or 0),
            scoreable_count=int(counts.get("pending") or 0),
            current_default_monthly_budget_cents=int(
                post["proposed_monthly_budget_cents"]
            ),
        )

    try:
        sister, evaluation_counts = create_related_role_record(
            db,
            role_id=int(source.id),
            organization_id=int(current_user.organization_id),
            creator_user_id=int(current_user.id),
            name=data.name,
            job_spec_text=data.job_spec_text,
            monthly_budget_cents=int(
                data.related_role_authorization.approved_monthly_budget_cents
            ),
            authorize_evaluation_counts=authorize_created_scope,
        )
    except HTTPException:
        db.rollback()
        raise
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
    data: SisterRoleRescoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        # Read identity without locking, then acquire the canonical owner before
        # the related-role row. Creation uses the same owner-first lock order.
        probe = require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
            lock_for_update=False,
        )
        if str(probe.role_kind or "") != ROLE_KIND_SISTER or not probe.ats_owner_role_id:
            raise HTTPException(status_code=409, detail="Role is not a coupled related role")
        source = _source_role(
            db,
            role_id=int(probe.ats_owner_role_id),
            organization_id=int(current_user.organization_id),
            lock_for_update=True,
        )
        role = require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
        )
        if int(role.ats_owner_role_id or 0) != int(source.id):
            raise HTTPException(status_code=409, detail="Role is not a coupled related role")
        assert_role_version(role, expected_version=data.expected_version)
        current = _scoring_status(db, role)
        require_related_role_rescore_scope(
            approved_max_scoreable_count=data.approved_max_scoreable_count,
            source_role=source,
            related_role=role,
            candidates_total=current.cohort_total,
            scoreable_count=current.cohort_scoreable,
        )
        prepared = ensure_sister_evaluations(db, role, reset_existing=True)
        require_related_role_rescore_scope(
            approved_max_scoreable_count=data.approved_max_scoreable_count,
            source_role=source,
            related_role=role,
            candidates_total=int(prepared.get("total") or 0),
            scoreable_count=int(prepared.get(SISTER_EVAL_PENDING) or 0),
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
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
    # This endpoint is polled every three seconds while scoring is active.
    # Return aggregate cohort counts here; exact CV-content hashing belongs to
    # the one-shot legacy-recovery preview and the final locked mutation check.
    roster_scope = related_role_scope_counts(db, role)
    source_scope = related_pipeline.valid_source_scope(
        organization_id=role.organization_id,
        owner_role_id=int(role.ats_owner_role_id),
    )
    rows = (
        db.query(
            SisterRoleEvaluation.status,
            func.count(SisterRoleEvaluation.id),
            func.sum(
                case(
                    (SisterRoleEvaluation.role_fit_score.is_not(None), 1),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (SisterRoleEvaluation.last_error_code == "authority_blocked", 1),
                    else_=0,
                )
            ),
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .filter(
            SisterRoleEvaluation.role_id == role.id,
            SisterRoleEvaluation.organization_id == role.organization_id,
            source_scope,
        )
        .group_by(SisterRoleEvaluation.status)
        .all()
    )
    counts = Counter({str(key): int(value) for key, value, _, _ in rows})
    stale_scores_visible = sum(
        int(with_score or 0)
        for key, _, with_score, _ in rows
        if str(key) == SISTER_EVAL_STALE
    )
    authority_waiting = sum(
        int(blocked or 0)
        for key, _, _, blocked in rows
        if str(key) == SISTER_EVAL_RETRY_WAIT
    )
    for key in (
        SISTER_EVAL_PENDING, SISTER_EVAL_RUNNING, SISTER_EVAL_RETRY_WAIT, SISTER_EVAL_DONE,
        SISTER_EVAL_ERROR, SISTER_EVAL_UNSCORABLE, SISTER_EVAL_EXCLUDED,
        SISTER_EVAL_STALE,
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
    waiting_reason = None
    if authority_waiting:
        waiting_reason = (
            role_paid_ats_work_block_reason(role, db=db)
            or "authority_blocked"
        )
    elif counts[SISTER_EVAL_RETRY_WAIT]:
        waiting_reason = "temporary_retry"
    elif counts[SISTER_EVAL_STALE]:
        waiting_reason = "rescore_approval_required"
    if (
        counts[SISTER_EVAL_RUNNING]
        or counts[SISTER_EVAL_PENDING]
    ):
        overall = "running"
    elif counts[SISTER_EVAL_RETRY_WAIT]:
        overall = "waiting"
    elif counts[SISTER_EVAL_STALE]:
        overall = "stale"
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
            SisterRoleEvaluation.organization_id == role.organization_id,
            related_pipeline.valid_source_scope(
                organization_id=role.organization_id,
                owner_role_id=int(role.ats_owner_role_id),
            ),
        )
        .order_by(SisterRoleEvaluation.role_fit_score.desc().nullslast())
        .limit(5)
        .all()
    )
    return SisterRoleScoringStatus(
        role_id=role.id,
        role_version=int(role.version or 1),
        cohort_total=int(roster_scope["total"]),
        cohort_scoreable=int(roster_scope["scoreable"]),
        cohort_unscorable=int(roster_scope["unscorable"]),
        cohort_excluded=int(roster_scope["excluded"]),
        status=overall,
        counts=dict(counts),
        total=total,
        scoreable_total=scoreable_total,
        scored=counts[SISTER_EVAL_DONE],
        stale_scored=stale_scores_visible,
        visible_scored=counts[SISTER_EVAL_DONE] + stale_scores_visible,
        completed=completed,
        progress_percent=round(
            (scoreable_completed / scoreable_total * 100.0)
            if scoreable_total
            else 100.0,
            1,
        ),
        waiting_reason=waiting_reason,
        estimated_rescore_cost_usd=round(
            int(roster_scope["scoreable"]) * RELATED_ROLE_SCORE_COST_USD,
            2,
        ),
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

    from .applications_routes import application_to_response, get_application

    app = get_application(application_id, current_user.organization_id, db)
    role, evaluation = move_related_role_application_stage(
        db,
        current_user=current_user,
        related_role_id=role_id,
        application=app,
        to_stage=data.pipeline_stage,
    )
    payload = application_to_response(
        app, use_cached_score_summary=True
    ).model_dump(mode="python")
    return project_sister_application(
        payload,
        sister_role=role,
        owner_role=db.get(Role, int(role.ats_owner_role_id)),
        evaluation=evaluation,
        db=db,
    )
