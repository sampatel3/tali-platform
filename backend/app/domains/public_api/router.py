"""Curated public API (``/public/v1``).

Authenticated with a Taali API key (see ``api_key_auth``). Every handler is
org-scoped to the key's organization, reusing the same isolation as the JWT
surface — an API key is just another way to resolve ``organization_id``.
Responses use the frozen schemas in ``schemas``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...domains.assessments_runtime.pipeline_service import role_pipeline_counts
from ...domains.assessments_runtime.role_support import get_application
from ...domains.identity_access.api_key_auth import get_api_principal, require_scope
from ...models.api_key import (
    ApiKey,
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ROLES_READ,
    SCOPE_SHARE_LINKS_WRITE,
)
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.share_link import ShareLink
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db
from ...services.pre_screening_snapshot import pre_screen_snapshot
from .schemas import (
    CreatePublicShareLink,
    PublicApplication,
    PublicApplicationList,
    PublicAssessment,
    PublicCandidate,
    PublicRole,
    PublicRoleList,
    PublicShareLink,
    PublicTaskSummary,
    PublicTest,
    PublicTestList,
    RoleMetrics,
)

router = APIRouter(prefix="/public/v1", tags=["Public API"])

# Public share links: a small, safe subset of the in-app expiry presets.
# ``single-view`` is deliberately excluded from the public surface for now.
_SHARE_EXPIRY: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
_PUBLIC_SHARE_MODES = frozenset({"client", "recruiter"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


# ---- Tests (assessment catalog) -------------------------------------------
@router.get("/tests", response_model=PublicTestList)
def list_tests(
    principal: ApiKey = Depends(require_scope(SCOPE_ROLES_READ)),
    db: Session = Depends(get_db),
):
    """The org's available assessment tasks (active org tasks + active
    platform templates). ``id`` is the stable ``task_key`` where present."""
    tasks = (
        db.query(Task)
        .filter(
            Task.is_active.is_(True),
            (
                (Task.organization_id == principal.organization_id)
                | (Task.is_template.is_(True))
            ),
        )
        .order_by(Task.name.asc())
        .all()
    )
    return PublicTestList(
        tests=[
            PublicTest(
                id=t.task_key or str(t.id),
                name=t.name,
                role=t.role,
                duration_minutes=t.duration_minutes,
            )
            for t in tasks
        ]
    )


# ---- Roles ----------------------------------------------------------------
def _role_to_public(role: Role) -> PublicRole:
    return PublicRole(
        id=role.id,
        name=role.name,
        description=role.description,
        source=role.source,
        workable_job_id=role.workable_job_id,
        created_at=role.created_at,
        tasks=[
            PublicTaskSummary(id=t.id, task_key=t.task_key, name=t.name)
            for t in (role.tasks or [])
        ],
    )


@router.get("/roles", response_model=PublicRoleList)
def list_roles(
    principal: ApiKey = Depends(require_scope(SCOPE_ROLES_READ)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    roles = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.organization_id == principal.organization_id)
        .order_by(Role.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return PublicRoleList(roles=[_role_to_public(r) for r in roles])


@router.get("/roles/{role_id}", response_model=PublicRole)
def get_role(
    role_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_ROLES_READ)),
    db: Session = Depends(get_db),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(
            Role.id == role_id,
            Role.organization_id == principal.organization_id,
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return _role_to_public(role)


# ---- Role applications + metrics ------------------------------------------
def _role_or_404(db: Session, role_id: int, organization_id: int) -> Role:
    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.organization_id == organization_id)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.get("/roles/{role_id}/applications", response_model=PublicApplicationList)
def list_role_applications(
    role_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_APPLICATIONS_READ)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workable_stage: Optional[str] = Query(default=None),
    pipeline_stage: Optional[str] = Query(default=None),
):
    """A role's candidate applications — each with Taali's signal + the synced
    Workable stage. Optional filters: ``workable_stage``, ``pipeline_stage``."""
    _role_or_404(db, role_id, principal.organization_id)
    q = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == principal.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if workable_stage:
        q = q.filter(CandidateApplication.workable_stage == workable_stage)
    if pipeline_stage:
        q = q.filter(CandidateApplication.pipeline_stage == pipeline_stage)
    total = q.count()
    apps = (
        q.order_by(CandidateApplication.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return PublicApplicationList(
        applications=[_application_to_public(a) for a in apps],
        total=int(total or 0),
    )


@router.get("/roles/{role_id}/metrics", response_model=RoleMetrics)
def role_metrics(
    role_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_APPLICATIONS_READ)),
    db: Session = Depends(get_db),
):
    """Job metrics: total applications, the canonical Taali funnel
    (applied/scored/invited/completed/advanced/rejected), decision outcomes,
    and the Workable hiring-funnel stage distribution (synced from Workable)."""
    _role_or_404(db, role_id, principal.organization_id)
    org_id = principal.organization_id
    base_filters = (
        CandidateApplication.organization_id == org_id,
        CandidateApplication.role_id == role_id,
        CandidateApplication.deleted_at.is_(None),
    )
    total = (
        db.query(func.count(CandidateApplication.id)).filter(*base_filters).scalar()
        or 0
    )
    outcome_rows = (
        db.query(
            CandidateApplication.application_outcome,
            func.count(CandidateApplication.id),
        )
        .filter(*base_filters)
        .group_by(CandidateApplication.application_outcome)
        .all()
    )
    by_outcome = {str(k or "unknown"): int(v or 0) for k, v in outcome_rows}
    workable_rows = (
        db.query(
            CandidateApplication.workable_stage,
            func.count(CandidateApplication.id),
        )
        .filter(*base_filters, CandidateApplication.workable_stage.isnot(None))
        .group_by(CandidateApplication.workable_stage)
        .all()
    )
    by_workable = {str(k): int(v or 0) for k, v in workable_rows if k}
    return RoleMetrics(
        role_id=role_id,
        total_applications=int(total),
        taali_funnel=role_pipeline_counts(db, organization_id=org_id, role_id=role_id),
        by_application_outcome=by_outcome,
        by_workable_stage=by_workable,
    )


# ---- Applications ---------------------------------------------------------
def _application_to_public(app: CandidateApplication) -> PublicApplication:
    try:
        snap = pre_screen_snapshot(app) or {}
    except Exception:  # pragma: no cover — recommendation is best-effort
        snap = {}
    cand = app.candidate
    return PublicApplication(
        id=app.id,
        status=app.status,
        pipeline_stage=app.pipeline_stage,
        application_outcome=app.application_outcome,
        candidate=(
            PublicCandidate(
                id=cand.id,
                full_name=getattr(cand, "full_name", None),
                email=getattr(cand, "email", None),
            )
            if cand
            else None
        ),
        role_id=app.role_id,
        role_name=app.role.name if app.role else None,
        cv_match_score=app.cv_match_score,
        pre_screen_score_100=app.pre_screen_score_100,
        requirements_fit_score_100=app.requirements_fit_score_100,
        taali_score_100=app.taali_score_cache_100,
        recommendation=snap.get("pre_screen_recommendation")
        or app.pre_screen_recommendation,
        workable_stage=app.workable_stage,
        workable_disqualified=app.workable_disqualified,
        workable_score=app.workable_score,
        created_at=app.created_at,
    )


@router.get("/applications/{application_id}", response_model=PublicApplication)
def get_public_application(
    application_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_APPLICATIONS_READ)),
    db: Session = Depends(get_db),
):
    app = get_application(application_id, principal.organization_id, db)
    return _application_to_public(app)


# ---- Assessments ----------------------------------------------------------
@router.get("/assessments/{assessment_id}", response_model=PublicAssessment)
def get_public_assessment(
    assessment_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_ASSESSMENTS_READ)),
    db: Session = Depends(get_db),
):
    a = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == principal.organization_id,
        )
        .first()
    )
    if a is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return PublicAssessment(
        id=a.id,
        status=_enum_value(a.status),
        role_id=a.role_id,
        task_id=a.task_id,
        candidate_id=a.candidate_id,
        application_id=a.application_id,
        created_at=a.created_at,
        started_at=a.started_at,
        completed_at=a.completed_at,
        expires_at=a.expires_at,
        scored_at=a.scored_at,
        taali_score=a.taali_score,
        final_score=a.final_score,
        assessment_score=a.assessment_score,
    )


# ---- Share links (the results_url the Workable provider returns) ----------
@router.post(
    "/applications/{application_id}/share-links",
    response_model=PublicShareLink,
)
def create_public_share_link(
    application_id: int,
    payload: CreatePublicShareLink,
    principal: ApiKey = Depends(require_scope(SCOPE_SHARE_LINKS_WRITE)),
    db: Session = Depends(get_db),
):
    if payload.mode not in _PUBLIC_SHARE_MODES:
        raise HTTPException(status_code=400, detail="mode must be 'client' or 'recruiter'")
    if payload.expiry not in _SHARE_EXPIRY:
        raise HTTPException(status_code=400, detail="expiry must be '24h', '7d', or '30d'")

    # 404s if the application isn't in the key's org — reuses the same
    # org-safe fetch the JWT surface uses.
    app = get_application(application_id, principal.organization_id, db)
    link = ShareLink(
        organization_id=app.organization_id,
        application_id=app.id,
        created_by_user_id=None,
        token=f"shr_{secrets.token_urlsafe(24)}",
        mode=payload.mode,
        expiry_preset=payload.expiry,
        expires_at=_utcnow() + _SHARE_EXPIRY[payload.expiry],
    )
    db.add(link)
    db.commit()
    db.refresh(link)

    base = (settings.FRONTEND_URL or "").rstrip("/")
    return PublicShareLink(
        id=link.id,
        application_id=link.application_id,
        token=link.token,
        url=f"{base}/share/{link.token}",
        mode=link.mode,
        expires_at=link.expires_at.isoformat() if link.expires_at else None,
        created_at=link.created_at.isoformat() if link.created_at else None,
    )
