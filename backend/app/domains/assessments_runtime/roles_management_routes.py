from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ...models.job_hiring_team import (
    TEAM_ROLE_HIRING_MANAGER,
    JobHiringTeam,
)
from ...models.organization import Organization
from ...models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, ROLE_KIND_SISTER, Role
from ...models.role_change_event import RoleChangeEvent
from ...models.role_brief import RoleBrief
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.role import (
    JobStatusUpdate,
    RoleClientUpdate,
    RoleCreate,
    RoleJobSpecUpdate,
    RoleJobSpecUpdateResponse,
    RoleResponse,
    RoleUpdate,
    RoleVersionCommand,
)
from ...services.application_events import on_role_jd_attached
from ...services.agent_control_ats_fence import require_authorized_agent_control_transaction_fence
from ...services.agent_policy_settings import (
    GRANULAR_AUTOMATION_FIELDS,
    SCORE_ONLY_ROLE_AUTOMATION_MESSAGE,
    activation_policy_values,
    apply_workspace_agent_defaults,
    role_is_score_only,
    role_automation_enabled,
)
from ...services.document_service import process_document_upload
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.job_page_lifecycle import role_accepts_native_applications
from ...services.role_criteria_service import (
    sync_all_criteria,
    sync_derived_criteria,
)
from ...services.role_concurrency import (
    assert_role_version,
    bump_role_version,
    role_query_for_update,
)
from ...services import role_family_reject_authority
from ...services import related_role_spec_lifecycle
from ...services.role_activation_command import (
    ExplicitAssessmentChoiceRequired,
    apply_durable_activation_policy,
    capture_activation_compensation_state,
    compensate_failed_activation_dispatch,
    resolve_activation_assessment_action,
    resolve_reconfiguration_as_skipped,
)
from ...services.sister_role_service import pipeline_counts_for_role, related_role_pipeline_counts_bulk
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_DISABLED,
    ROLE_CHANGE_ACTION_AGENT_ENABLED,
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
    list_role_change_events,
    serialize_role_change_event,
)
from ...platform.request_context import get_request_id
from .role_catalogue_order import load_role_catalogue_page, order_roles_by_family_name
from .role_support import get_role, role_family_load_options, role_to_response
from .job_authorization import JobPermission, require_job_permission
from .pipeline_service import role_pipeline_counts_bulk
from ..agentic._hub_shared import role_pending_decisions_by_type
from .role_collection_queries import apply_role_search, count_roles, role_relationship_counts, role_task_counts
from .role_management_route_support import (
    _add_role_change_boundary as _add_role_change_boundary,
)
from .role_task_provisioning_support import (
    maybe_autogenerate_assessment_task as _maybe_autogenerate_assessment_task,
    request_autogenerate_assessment_task as _request_autogenerate_assessment_task,
)
from . import role_threshold_support
from .role_activation_update_preflight import (
    DirectActivationPreparation,
    apply_prepared_direct_activation_task,
    prepare_direct_role_activation,
)

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")

@router.get("/roles/{role_id}/change-events")
def get_role_change_events(
    role_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Newest-first audit history for one shared job workspace."""

    try:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.VIEW,
        )
    except HTTPException as exc:
        # Hard-deleted roles intentionally leave immutable history behind.
        # Only a workspace owner may recover that history without the live
        # Role row that normally establishes tenant membership.
        has_retained_history = (
            db.query(RoleChangeEvent.id)
            .filter(
                RoleChangeEvent.organization_id
                == int(current_user.organization_id),
                RoleChangeEvent.role_id == role_id,
            )
            .first()
            is not None
        )
        if (
            exc.status_code != 403
            or getattr(current_user, "role", None) != "owner"
            or not has_retained_history
        ):
            raise
    events = list_role_change_events(
        db,
        organization_id=int(current_user.organization_id),
        role_id=role_id,
        limit=limit,
        before_id=before_id,
    )
    actor_ids = {
        int(event.actor_user_id)
        for event in events
        if event.actor_user_id is not None
    }
    actors = (
        {
            int(user.id): {
                "user_id": int(user.id),
                "name": user.full_name,
                "email": user.email,
            }
            for user in db.query(User)
            .filter(
                User.id.in_(actor_ids),
                User.organization_id == current_user.organization_id,
            )
            .all()
        }
        if actor_ids
        else {}
    )
    payload = []
    for event in events:
        row = serialize_role_change_event(event)
        row["actor"] = (
            actors.get(int(event.actor_user_id))
            if event.actor_user_id is not None
            else None
        )
        payload.append(row)
    return payload


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Fall back to the org-wide defaults set on the Settings → AI agent tab
    # when the create request doesn't supply its own values. Recruiters can
    # edit any of these afterwards from the role page; the defaults are a
    # starting point, not a binding link, and existing roles are not
    # rewritten when the workspace defaults change.
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .first()
    )

    # When the request supplies explicit ``additional_requirements`` text we
    # honour it (legacy callers / Workable import). When it doesn't and the
    # org has chip-based defaults, ``sync_all_criteria`` snapshots them
    # New roles inherit workspace criteria via ``snapshot_workspace_criteria``
    # in ``sync_all_criteria`` below.
    role = Role(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        description=(data.description or None),
        screening_pack_template=(data.screening_pack_template.model_dump() if data.screening_pack_template else None),
        tech_interview_pack_template=(data.tech_interview_pack_template.model_dump() if data.tech_interview_pack_template else None),
        workable_actor_member_id=(data.workable_actor_member_id or None),
    )
    apply_workspace_agent_defaults(
        role,
        org,
        explicit_budget_cents=data.monthly_usd_budget_cents,
        explicit_score_threshold=data.score_threshold,
    )
    db.add(role)
    try:
        db.flush()
        # The creator owns the new job workspace and can seed its wider hiring
        # team. Without this row a fail-closed per-job policy would strand a
        # role created by a non-owner.
        db.add(
            JobHiringTeam(
                organization_id=int(current_user.organization_id),
                role_id=int(role.id),
                user_id=int(current_user.id),
                team_role=TEAM_ROLE_HIRING_MANAGER,
            )
        )
        sync_all_criteria(db, role)
        # Persist the async generation intent in the same transaction as the
        # role. If the post-commit broker kick is lost, Beat recovers it.
        _request_autogenerate_assessment_task(role, reason="manual_role_create")
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create role")

    # Auto-provision a draft assessment task from the role's JD (gated;
    # default on). Off-request via Celery — generation is slow + paid.
    _maybe_autogenerate_assessment_task(role)
    return role_to_response(role)


@router.get("/roles")
def list_roles(
    response: Response,
    include_pipeline_stats: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=200),
    include_total: bool = Query(default=False),
    sort_by: str = Query(default="activity", pattern="^(activity|name)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    roles_query = (
        db.query(Role)
        # Tasks/criteria are NOT loaded here: the list serializes with
        # summary=True and relationship counts/effective task state are batched
        # below. Avoiding task hydration also avoids transferring repository
        # definitions the list never renders.
        .options(
            *role_family_load_options(
                organization_id=int(current_user.organization_id)
            ),
        )
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
    )
    if sort_by == "name":
        roles_query = order_roles_by_family_name(roles_query)
    else:
        roles_query = roles_query.order_by(
            Role.starred_for_auto_sync.desc(),
            Role.updated_at.desc().nullslast(),
            Role.created_at.desc(),
            Role.id.desc(),
        )
    roles_query = apply_role_search(roles_query, search)
    if include_total:
        response.headers["X-Total-Count"] = str(count_roles(db, organization_id=current_user.organization_id, search=search))
    # Keep every collection read bounded. Name-sorted pages may grow just past
    # ``limit`` to avoid splitting a role family; callers advance by the actual
    # number of returned rows, so the next offset remains stable.
    roles = load_role_catalogue_page(
        roles_query,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    if not roles:
        return []

    role_ids = [role.id for role in roles]
    task_counts, sister_counts, active_task_counts = role_relationship_counts(
        db,
        role_ids,
        organization_id=int(current_user.organization_id),
    )
    operational_role_ids = list({
        int(role.ats_owner_role_id or role.id) for role in roles
    })
    app_counts_rows = (
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.role_id.in_(operational_role_ids),
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    app_counts = {int(role_id): int(total) for role_id, total in app_counts_rows}
    active_counts: dict[int, int] = {}
    last_activity_by_role: dict[int, datetime | None] = {}
    stage_counts_by_role: dict[int, dict[str, int]] = {}

    if include_pipeline_stats:
        active_rows = (
            db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
            .filter(
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.role_id.in_(operational_role_ids),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
        active_counts = {int(role_id): int(total) for role_id, total in active_rows}

        last_activity_rows = (
            db.query(
                CandidateApplication.role_id,
                func.max(
                    func.coalesce(
                        CandidateApplication.pipeline_stage_updated_at,
                        CandidateApplication.updated_at,
                        CandidateApplication.created_at,
                    )
                ),
            )
            .filter(
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(operational_role_ids),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
        last_activity_by_role = {int(role_id): ts for role_id, ts in last_activity_rows}
        # Batched: two queries for the whole role list instead of the 2×N the
        # per-role helper would issue. role_pipeline_counts_bulk mirrors
        # role_pipeline_counts exactly (zero-filled, "rejected" included) and is
        # locked to it by test_role_pipeline_counts_bulk.
        stage_counts_by_role = role_pipeline_counts_bulk(
            db,
            organization_id=current_user.organization_id,
            role_ids=operational_role_ids,
        )
        stage_counts_by_role.update(related_role_pipeline_counts_bulk(db, [int(role.id) for role in roles if str(role.role_kind or "") == ROLE_KIND_SISTER]))

    # Batched role -> client lookup (one query) for the Jobs list's Client column
    # + filter. Roles with no requisition (or no client) are simply absent.
    from ...services.role_brief_service import role_client_map

    clients_by_role = role_client_map(
        db, organization_id=current_user.organization_id, role_ids=role_ids
    )

    # Batched "has an OPEN public job page" — one DISTINCT query joining
    # JobPage → RoleBrief for the whole page, not a per-card lookup. Drives the
    # Jobs list "Live" badge (role has a live /job/{token} apply page).
    published_page_role_ids = {
        int(rid)
        for (rid,) in (
            db.query(RoleBrief.role_id)
            .join(JobPage, JobPage.brief_id == RoleBrief.id)
            .filter(
                RoleBrief.organization_id == current_user.organization_id,
                RoleBrief.role_id.in_(role_ids),
                JobPage.status == JOB_PAGE_STATUS_OPEN,
            )
            .distinct()
            .all()
        )
        if rid is not None
    }
    # An OPEN JobPage can be a preview while the managed role is still draft,
    # its agent is off/paused, or public apply is globally disabled. The Jobs
    # list's ``is_published`` field drives its Live badge, so use the same
    # fail-closed intake policy as the public apply and distribution routes.
    live_native_role_ids = {
        int(role.id)
        for role in roles
        if int(role.id) in published_page_role_ids
        and settings.ATS_PUBLIC_APPLY_ENABLED
        and role_accepts_native_applications(role, db=db)
    }

    return [
        role_to_response(
            role,
            summary=True,
            tasks_count=task_counts.get(role.id, 0),
            has_active_task=active_task_counts.get(role.id, 0) > 0,
            sister_role_count=sister_counts.get(role.id, 0),
            applications_count=app_counts.get(int(role.ats_owner_role_id or role.id), 0),
            stage_counts=pipeline_counts_for_role(db, role, organization_id=current_user.organization_id, standard_counts=stage_counts_by_role.get(int(role.id), {})),
            active_candidates_count=active_counts.get(int(role.ats_owner_role_id or role.id), 0),
            last_candidate_activity_at=last_activity_by_role.get(int(role.ats_owner_role_id or role.id)),
            client=clients_by_role.get(role.id),
            is_published=role.id in live_native_role_ids,
        )
        for role in roles
    ]


def _serialize_role_detail(db: Session, role: Role, organization_id: int) -> RoleResponse:
    """The full role-detail payload: funnel counts + pending-decision chips + the
    linked requisition's structured spec. Shared by GET /roles/{id} and the
    job-status mutation so both stay in lock-step."""
    operational_role_id = int(role.ats_owner_role_id or role.id)
    app_count = (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == operational_role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    # Per-stage funnel counts (applied → invited → in_assessment → review →
    # advanced + rejected) — the same aggregate the /roles list attaches, so
    # the role detail page can render the home-card funnel summary from the
    # single GET rather than deriving it from the (row-capped) applications list.
    stage_counts = pipeline_counts_for_role(db, role, organization_id=organization_id)
    # Pending agent decisions by type — feeds the role funnel's "awaiting your
    # decision" chips (uncapped, unlike the row-limited applications fetch).
    pending_decisions_by_type = role_pending_decisions_by_type(
        db, organization_id=organization_id, role_id=role.id
    )
    # The linked requisition's structured spec (None unless this role came from /
    # was linked to a requisition). Detail-only — drives the role's Job Spec tab.
    from ...services.role_brief_service import requisition_spec_for_role, role_client_map

    requisition = requisition_spec_for_role(
        db, organization_id=organization_id, role_id=role.id
    )
    client = role_client_map(
        db, organization_id=organization_id, role_ids=[role.id]
    ).get(role.id)
    is_published = bool(
        settings.ATS_PUBLIC_APPLY_ENABLED
        and role_accepts_native_applications(role, db=db)
        and db.query(JobPage.id)
        .join(RoleBrief, RoleBrief.id == JobPage.brief_id)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.role_id == role.id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .first()
        is not None
    )
    return role_to_response(
        role,
        tasks_count=len(role.tasks or []),
        applications_count=int(app_count),
        stage_counts=stage_counts,
        pending_decisions_by_type=pending_decisions_by_type,
        requisition=requisition,
        client=client,
        is_published=is_published,
    )


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role_endpoint(
    role_id: int,
    shell: bool = Query(
        default=False,
        description="Return the lightweight role shell without pipeline aggregates.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Role).options(
        *role_family_load_options(
            organization_id=int(current_user.organization_id)
        )
    )
    if not shell:
        query = query.options(joinedload(Role.tasks))
    role = (
        query
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if shell:
        task_counts, active_task_counts = role_task_counts(db, [int(role.id)])
        return role_to_response(
            role,
            summary=True,
            include_provisioning=True,
            tasks_count=task_counts.get(int(role.id), 0),
            has_active_task=active_task_counts.get(int(role.id), 0) > 0,
            applications_count=0,
        )
    return _serialize_role_detail(db, role, current_user.organization_id)


@router.post("/roles/{role_id}/job-status", response_model=RoleResponse)
def set_job_status_endpoint(
    role_id: int,
    data: JobStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the job's lifecycle status (draft / open / filled / filled_external /
    cancelled).

    Closing outcomes remain recruiter-controlled. Opening a requisition-backed
    native page is different: it is an intake/spend boundary and cannot bypass
    the durable Turn-on readiness contract. The role must already have an active,
    unpaused agent whose runtime preflight still passes.
    """
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    # The brief link is the durable requisition-origin marker: Workable adoption
    # legitimately changes ``role.source`` but keeps this association. Do not
    # impose the agent Turn-on contract on unrelated legacy/manual roles merely
    # because a recruiter previously gave them a lifecycle status.
    is_requisition_role = bool(
        getattr(role, "source", None) == "requisition"
        or db.query(RoleBrief.id)
        .filter(
            RoleBrief.role_id == role.id,
            RoleBrief.organization_id == role.organization_id,
        )
        .first()
    )
    if data.status == JOB_STATUS_OPEN and is_requisition_role:
        from ...services.ats_role_lifecycle import ats_job_lifecycle

        external_job = ats_job_lifecycle(role)
        if external_job.external_job_id and external_job.external_job_live is False:
            provider_label = str(external_job.provider or "ATS").title()
            external_state = external_job.external_job_state or "closed/deleted"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This native mirror cannot reopen while its linked {provider_label} "
                    f"job is {external_state}. Reopen the job in {provider_label} first."
                ),
            )
        if not bool(role.agentic_mode_enabled):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This native job can open only after its agent is ready. "
                    "Use Turn on; it will open applications automatically."
                ),
            )
        if role.agent_paused_at is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This native job cannot reopen while its agent is paused. "
                    "Resume the agent first."
                ),
            )
        from ...services.agent_activation_readiness import (
            activation_readiness,
            readiness_message,
        )

        readiness = activation_readiness(role)
        if not readiness.get("ready"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Agent runtime is not ready, so applications remain closed: "
                    f"{readiness_message(readiness)}."
                ),
            )
    previous = role.job_status
    audit_before = capture_role_change_snapshot(role)
    role.job_status = data.status
    if role.job_status != previous:
        _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="job_status_updated",
            reason=data.reason or f"job status changed from {previous} to {data.status}",
            before=audit_before,
        )
    db.commit()
    db.refresh(role)
    logger.info(
        "Role %s job_status %s -> %s by user %s%s",
        role.id,
        previous,
        data.status,
        current_user.id,
        f" ({data.reason})" if data.reason else "",
    )
    return _serialize_role_detail(db, role, current_user.organization_id)


@router.post("/roles/{role_id}/client", response_model=RoleResponse)
def set_role_client_endpoint(
    role_id: int,
    data: RoleClientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign (or clear, with ``client_id=null``) the consultancy client a role
    belongs to — including legacy / Workable-imported roles that never went
    through a requisition. The link is stored on the role's brief (a minimal stub
    is created when none exists) so the Jobs Client column / filter and per-client
    rollups pick the role up."""
    from ...services.role_brief_service import set_role_client

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    set_role_client(
        db,
        organization_id=current_user.organization_id,
        role_id=role.id,
        client_id=data.client_id,
    )
    _add_role_change_boundary(
        db,
        role=role,
        current_user=current_user,
        action="role_client_updated",
        reason="job client assignment updated",
    )
    db.commit()
    db.refresh(role)
    logger.info(
        "Role %s client -> %s by user %s",
        role.id,
        data.client_id,
        current_user.id,
    )
    return _serialize_role_detail(db, role, current_user.organization_id)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updates = data.model_dump(exclude_unset=True)
    expected_version = int(updates.pop("expected_version"))
    expected_role_family = updates.pop("expected_role_family", None)
    if updates.get("agentic_mode_enabled") is False:
        require_authorized_agent_control_transaction_fence(
            db, current_user=current_user, role_id=role_id
        )
    preflight = prepare_direct_role_activation(
        db,
        current_user=current_user,
        role_id=role_id,
        expected_version=expected_version,
        updates=updates,
    )
    try:
        return _update_role_command(
            role_id,
            db=db,
            current_user=current_user,
            updates=updates,
            expected_version=expected_version,
            expected_role_family=expected_role_family,
            activation_preflight=preflight,
        )
    finally:
        if preflight is not None:
            preflight.release()


def _update_role_command(
    role_id: int,
    *,
    db: Session,
    current_user: User,
    updates: dict,
    expected_version: int,
    expected_role_family,
    activation_preflight: DirectActivationPreparation | None,
):
    agent_control_fields = {
        "agentic_mode_enabled",
        "agent_action_allowlist",
        "agent_token_budget_per_cycle",
        "agent_decision_budget_per_cycle",
        "activation_assessment_action",
        "monthly_usd_budget_cents",
        "auto_reject",
        "auto_reject_pre_screen",
        "auto_promote",
        *GRANULAR_AUTOMATION_FIELDS,
        "auto_skip_assessment",
    }
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=(
            JobPermission.CONTROL_AGENT
            if agent_control_fields.intersection(updates)
            else JobPermission.EDIT_ROLE
        ),
    )
    if updates.get("auto_reject") is True or updates.get("auto_reject_pre_screen") is True:
        current_family = role_family_reject_authority.lock_current_role_families(
            db,
            organization_id=int(current_user.organization_id),
            role_ids=[int(role_id)],
        ).get(int(role_id))
        if current_family is not None:
            role_family_reject_authority.require_expected_role_family(
                expected=expected_role_family,
                current=current_family,
            )
    # Serialize every shared Role write, then reject a caller that edited an
    # older snapshot.  The explicit version—not the lock alone—is what stops a
    # queued/stale browser request from becoming a silent last-write-wins save.
    role = (
        role_query_for_update(
            db,
            role_id=role_id,
            organization_id=current_user.organization_id,
        )
        .options(selectinload(Role.tasks))
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    assert_role_version(
        role,
        expected_version=expected_version,
        current_role=lambda: role_to_response(role).model_dump(mode="json"),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    audit_before = capture_role_change_snapshot(role)
    audit_from_version = int(role.version or 1)
    try:
        activation_assessment_action = resolve_activation_assessment_action(
            role, updates, updates.pop("activation_assessment_action", None)
        )
    except ExplicitAssessmentChoiceRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if role_is_score_only(role):
        unsafe_automation = {
            key
            for key in (
                "auto_reject",
                "auto_reject_pre_screen",
                "auto_promote",
                *GRANULAR_AUTOMATION_FIELDS,
            )
            if updates.get(key) is True
        }
        if unsafe_automation:
            raise HTTPException(
                status_code=409,
                detail=SCORE_ONLY_ROLE_AUTOMATION_MESSAGE,
            )
    if activation_assessment_action and not bool(
        updates.get("agentic_mode_enabled")
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "activation_assessment_action is valid only while turning "
                "the agent on"
            ),
        )
    if activation_assessment_action == "approve_when_ready":
        if bool(role.agentic_mode_enabled):
            raise HTTPException(status_code=409, detail="The agent is already enabled")
        incoming_budget = updates.get(
            "monthly_usd_budget_cents", role.monthly_usd_budget_cents
        )
        if incoming_budget is None or int(incoming_budget) <= 0:
            raise HTTPException(
                status_code=422,
                detail="monthly_usd_budget_cents is required to enable agentic mode",
            )
        from ...services.role_activation_intent import (
            activation_intent_state,
            activation_intent_task_ready,
            request_role_activation_intent,
        )

        activation_policy = apply_durable_activation_policy(role, updates)
        intent = request_role_activation_intent(
            role,
            user_id=int(current_user.id),
            monthly_budget_cents=int(incoming_budget),
            auto_promote=activation_policy["auto_promote"],
            auto_send_assessment=activation_policy["auto_send_assessment"],
            auto_resend_assessment=activation_policy[
                "auto_resend_assessment"
            ],
            auto_advance=activation_policy["auto_advance"],
        )
        if capture_role_change_snapshot(role) != audit_before:
            audit_to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=audit_before,
                action=ROLE_CHANGE_ACTION_UPDATED,
                actor_user_id=int(current_user.id),
                from_version=audit_from_version,
                to_version=audit_to_version,
                reason="agent activation requested while assessment provisioning completes",
                request_id=get_request_id(),
            )
        try:
            db.commit()
            db.refresh(role)
        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=500, detail="Failed to persist agent activation request"
            )

        # Both kicks are latency optimisations only. Generation + activation
        # remain durable in Role JSON and the minute sweep retries any broker
        # rejection without requiring this request or browser tab to survive.
        try:
            if activation_intent_task_ready(role):
                from ...tasks.agent_tasks import agent_cohort_tick_role

                agent_cohort_tick_role.delay(
                    int(role.id),
                    activation=True,
                    activation_intent_id=str(intent["request_id"]),
                )
            elif not list(role.tasks or []):
                from ...tasks.assessment_tasks import generate_assessment_task_for_role

                generate_assessment_task_for_role.delay(
                    int(role.id), int(role.organization_id)
                )
        except Exception:
            logger.warning(
                "Initial durable Turn-on kick failed role_id=%s; sweep will retry",
                role.id,
                exc_info=True,
            )
        # Reload the nested intent in case a Celery-eager test completed it
        # synchronously; production normally returns the honest OFF/pending row.
        db.expire_all()
        role = get_role(role_id, current_user.organization_id, db)
        _ = activation_intent_state(role)
        return _serialize_role_detail(db, role, current_user.organization_id)
    # The role threshold is the downstream full-score decision boundary.
    # Snapshot its effective value before mutating so an actual move can
    # re-flow deterministic full-score cards without re-scoring candidates.
    _threshold_may_change = (
        "score_threshold" in updates or "auto_reject_threshold_mode" in updates
    )
    _threshold_before = None
    if _threshold_may_change:
        try:
            _threshold_before = role_threshold_support.effective_role_fit_threshold(db, role)
        except Exception:
            # No safe baseline to compare against → don't reconcile (and
            # never block the role edit itself on a threshold-resolution error).
            logger.exception(
                "Role-fit threshold (pre-update) resolution failed for role_id=%s", role.id
            )
            _threshold_may_change = False
    if "name" in updates and updates["name"] is not None:
        role.name = updates["name"].strip()
    if "description" in updates:
        role.description = updates["description"] or None
    if "screening_pack_template" in updates:
        template = updates["screening_pack_template"]
        role.screening_pack_template = template.model_dump() if template else None
    if "tech_interview_pack_template" in updates:
        template = updates["tech_interview_pack_template"]
        role.tech_interview_pack_template = template.model_dump() if template else None
    if "auto_reject_threshold_mode" in updates and updates["auto_reject_threshold_mode"] is not None:
        role.auto_reject_threshold_mode = str(updates["auto_reject_threshold_mode"])
    if "workable_actor_member_id" in updates:
        role.workable_actor_member_id = updates["workable_actor_member_id"] or None
    agent_activated_now = False
    agent_resumed_now = False
    agent_resume_requested = False
    automatic_budget_resume_check = False
    activation_previous = capture_activation_compensation_state(role)
    activation_approved_task_id: int | None = None
    if "agentic_mode_enabled" in updates:
        next_enabled = bool(updates["agentic_mode_enabled"])
        was_enabled = bool(role.agentic_mode_enabled)
        was_paused = role.agent_paused_at is not None
        if next_enabled:
            if activation_assessment_action and was_enabled:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The agent is already enabled; change the assessment "
                        "configuration from Agent settings instead."
                    ),
                )
            # Activating requires a monthly USD budget so the agent can't
            # quietly run up costs. Allow either a value already on the role
            # or one supplied in the same PATCH.
            incoming_budget = updates.get("monthly_usd_budget_cents", role.monthly_usd_budget_cents)
            if incoming_budget is None or int(incoming_budget) <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="monthly_usd_budget_cents is required to enable agentic mode",
                )
            if activation_assessment_action == "skip_assessment":
                from ...services.role_activation_intent import (
                    cancel_role_activation_intent,
                )
                cancel_role_activation_intent(
                    role,
                    user_id=int(current_user.id),
                    reason="assessment explicitly skipped during Turn on",
                )
                resolve_reconfiguration_as_skipped(
                    role,
                    user_id=int(current_user.id),
                )
                # Feed the effective path into both readiness below and the
                # normal field assignment later in this transaction.
                updates["auto_skip_assessment"] = True
            if activation_assessment_action == "approve_generated_task":
                activation_approved_task_id = apply_prepared_direct_activation_task(
                    db,
                    role=role,
                    preparation=activation_preflight,
                    organization_id=int(current_user.organization_id),
                    user_id=int(current_user.id),
                )
            # Worker/provider readiness ran in the preflight phase before any
            # Role or Task row lock. The exact Role version and optional task
            # fingerprint are revalidated in this transaction.
            if activation_preflight is None:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Turn on preflight became stale. Refresh the job and retry.",
                )
        role.agentic_mode_enabled = next_enabled
        agent_activated_now = next_enabled and not was_enabled
        # Snapshot each reversible-action choice at activation. Concrete role
        # settings survive Turn on; a truly legacy role with no granular values
        # materializes the same safe HITL default shown by the current UI.
        if agent_activated_now:
            activation_policy = activation_policy_values(role, updates)
            role.auto_promote = activation_policy["auto_promote"]
            for field in GRANULAR_AUTOMATION_FIELDS:
                setattr(role, field, activation_policy[field])
        # Agent-on implies "auto-sync this role" — keep the periodic
        # Workable fetch (comments, activities, questionnaire answers)
        # flowing so the agent's pre-screen + scoring see fresh signal
        # without the recruiter having to remember to star it. One-way:
        # disabling the agent doesn't unstar (star is sticky).
        if agent_activated_now and not role.starred_for_auto_sync:
            role.starred_for_auto_sync = True
        # A requisition publish creates a native draft role + live JobPage.
        # Turning its agent on is the explicit go-live action for the hiring
        # workflow, so it becomes OPEN even when the optional Workable bridge
        # has not been used.
        if (
            agent_activated_now
            and role.source == "requisition"
            and role.job_status == JOB_STATUS_DRAFT
        ):
            role.job_status = JOB_STATUS_OPEN
        # Resume = "was paused while enabled, now no longer paused while still
        # enabled". Distinct from activation. Both deserve an immediate cycle —
        # otherwise the recruiter clicks Resume and waits up to 60 minutes for
        # the next beat-scheduled tick to fire.
        agent_resume_requested = bool(next_enabled and was_paused)
        # Turning the agent OFF no longer discards its pending decisions. A
        # pending decision is a real recommendation about a real candidate
        # (reject / advance / send assessment) that the recruiter can still
        # action by hand — approving one executes the underlying action without
        # the agent running. Its lifecycle belongs to the candidate, not to the
        # agent's power state: the queue clears as it's actioned, when the
        # candidate closes, or when the card goes stale. Recruiters who want a
        # clean slate opt in explicitly via POST /agent-decisions/discard (the
        # "also discard" choice on the Turn-off dialog). The one-pending-per-
        # application invariant in queue_decision/pre_screen_decision_emitter
        # means re-enabling later won't duplicate these cards.
        if not next_enabled:
            from ...services.role_activation_intent import (
                cancel_role_activation_intent,
            )

            cancel_role_activation_intent(
                role,
                user_id=int(current_user.id),
                reason="agent turned off before deferred activation completed",
            )
    if "agent_action_allowlist" in updates:
        role.agent_action_allowlist = updates["agent_action_allowlist"]
    if "agent_token_budget_per_cycle" in updates:
        role.agent_token_budget_per_cycle = updates["agent_token_budget_per_cycle"]
    if "agent_decision_budget_per_cycle" in updates:
        role.agent_decision_budget_per_cycle = updates["agent_decision_budget_per_cycle"]
    if "monthly_usd_budget_cents" in updates:
        role.monthly_usd_budget_cents = updates["monthly_usd_budget_cents"]
        # Raising the cap above month-to-date spend should bring a
        # budget-paused role back on its own — the recruiter shouldn't have
        # to toggle the agent off/on. The cohort sweep skips paused roles,
        # so without this the raised cap has no effect until a manual
        # resume. Guarded inside the helper so a still-over-budget raise
        # won't resume only to re-pause next cycle. Mirrors the explicit
        # Resume path in the agentic_mode_enabled block above; the
        # `agent_resumed_now` flag kicks an immediate cycle below.
        automatic_budget_resume_check = True
    if "score_threshold" in updates:
        # ``score_threshold`` is the per-role override of the org default
        # used by both the agent's send-assessment decision rule and the
        # recruiter-facing pipeline distribution. PATCH was accepting the
        # field in the schema but never assigning it to the model, so
        # threshold changes from the UI silently no-op'd on existing
        # roles. Allow ``None`` to clear back to the org default.
        role.score_threshold = updates["score_threshold"]
    if "auto_reject" in updates and updates["auto_reject"] is not None:
        role.auto_reject = bool(updates["auto_reject"])
    if "auto_reject_pre_screen" in updates and updates["auto_reject_pre_screen"] is not None:
        role.auto_reject_pre_screen = bool(updates["auto_reject_pre_screen"])
    if "auto_promote" in updates and updates["auto_promote"] is not None:
        role.auto_promote = bool(updates["auto_promote"])
        # Compatibility for legacy clients: only fan the aggregate switch out
        # when there is no mixed action-level policy to preserve. This lets
        # pre-upgrade clients keep editing uniformly-backfilled roles without
        # allowing their aggregate field to erase a deliberate mixed policy.
        current_granular = [
            getattr(role, field, None) for field in GRANULAR_AUTOMATION_FIELDS
        ]
        explicit_granular = any(
            field in updates and updates.get(field) is not None
            for field in GRANULAR_AUTOMATION_FIELDS
        )
        concrete_values = {
            bool(value) for value in current_granular if value is not None
        }
        if not explicit_granular and len(concrete_values) <= 1:
            for field in GRANULAR_AUTOMATION_FIELDS:
                setattr(role, field, bool(updates["auto_promote"]))
    for field in GRANULAR_AUTOMATION_FIELDS:
        if field in updates and updates[field] is not None:
            setattr(role, field, bool(updates[field]))
    if any(
        getattr(role, field, None) is not None
        for field in GRANULAR_AUTOMATION_FIELDS
    ):
        role.auto_promote = all(
            role_automation_enabled(role, field)
            for field in GRANULAR_AUTOMATION_FIELDS
        )
    skip_assessment_changed = False
    if "auto_skip_assessment" in updates and updates["auto_skip_assessment"] is not None:
        skip_assessment_changed = bool(role.auto_skip_assessment) != bool(
            updates["auto_skip_assessment"]
        )
        role.auto_skip_assessment = bool(updates["auto_skip_assessment"])
    activation_policy_fields = {
        "monthly_usd_budget_cents",
        "auto_reject",
        "auto_reject_pre_screen",
        "auto_promote",
        *GRANULAR_AUTOMATION_FIELDS,
        "auto_skip_assessment",
        "score_threshold",
        "auto_reject_threshold_mode",
        "agent_action_allowlist",
        "agent_token_budget_per_cycle",
        "agent_decision_budget_per_cycle",
    }
    if activation_policy_fields.intersection(updates):
        # A saved Turn-on command is an authorization/outbox, not an immutable
        # policy snapshot. Keep it aligned with edits made while generation or
        # readiness is pending so its worker can never restore older, broader
        # automation settings over a recruiter's newer restrictions.
        from ...services.role_activation_intent import (
            refresh_role_activation_intent_policy,
        )

        refresh_role_activation_intent_policy(role)
    # Clearing a pause is a guarded state transition, even when a caller sends
    # ``agentic_mode_enabled=true`` explicitly.  This prevents an over-budget
    # or production-unready role from being unconditionally unpaused by the
    # generic PATCH.  Budget-only edits use the automatic mode, which may clear
    # a system budget/credit hold but must never undo "paused by recruiter".
    if agent_resume_requested or automatic_budget_resume_check:
        from ...agent_runtime import budget_guard

        if agent_resume_requested:
            agent_resumed_now = bool(
                budget_guard.resume_if_under_budget(
                    db, role=role, explicit=True
                )
            )
            if not agent_resumed_now:
                paused_reason = role.agent_paused_reason or "budget/runtime guard"
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The agent remains paused because its resume guard did "
                        f"not pass: {paused_reason}."
                    ),
                )
        elif budget_guard.resume_if_under_budget(
            db, role=role, explicit=False
        ):
            agent_resumed_now = True
    if "suppressed_org_criterion_ids" in updates:
        raw = updates["suppressed_org_criterion_ids"] or []
        role.suppressed_org_criterion_ids = [int(x) for x in raw]
    if (agent_activated_now or agent_resumed_now) and str(role.role_kind or "") != ROLE_KIND_SISTER:
        bootstrap_started_at = datetime.now(timezone.utc)
        role.agent_bootstrap_status = "starting"
        role.agent_bootstrap_error = None
        role.agent_bootstrap_started_at = bootstrap_started_at
        role.agent_bootstrap_completed_at = None
        if agent_activated_now:
            provisioning = (
                dict(role.assessment_task_provisioning)
                if isinstance(role.assessment_task_provisioning, dict)
                else {}
            )
            if not bool(role.interview_focus):
                provisioning["interview_focus_provisioning"] = {
                    "status": "pending",
                    "last_error": None,
                    "next_attempt_at": None,
                    "updated_at": bootstrap_started_at.isoformat(),
                }
            provisioning["tech_questions_provisioning"] = {
                "status": (
                    "succeeded" if bool(role.tech_questions_signature) else "pending"
                ),
                "last_error": None,
                "next_attempt_at": None,
                "updated_at": bootstrap_started_at.isoformat(),
            }
            role.assessment_task_provisioning = provisioning
    if capture_role_change_snapshot(role) != audit_before:
        audit_to_version = bump_role_version(role)
        audit_action = ROLE_CHANGE_ACTION_UPDATED
        if agent_activated_now:
            audit_action = ROLE_CHANGE_ACTION_AGENT_ENABLED
        elif (
            "agentic_mode_enabled" in updates
            and not bool(updates["agentic_mode_enabled"])
            and bool(audit_before.get("agentic_mode_enabled"))
        ):
            audit_action = ROLE_CHANGE_ACTION_AGENT_DISABLED
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=audit_action,
            actor_user_id=int(current_user.id),
            from_version=audit_from_version,
            to_version=audit_to_version,
            request_id=get_request_id(),
        )
    # Capture the exact revision produced by this command before the primary
    # commit releases its lock. Follow-up checklist/reconcile transactions may
    # expire and refresh the ORM object after another user has saved a newer
    # revision; that newer revision must never become our compensation token.
    dispatch_control_version = (
        int(audit_to_version)
        if agent_activated_now or agent_resumed_now
        else None
    )
    try:
        db.commit()
        db.refresh(role)
        if activation_preflight is not None:
            activation_preflight.release()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")
    reconcile_control_version = int(role.version or 1)
    # A settings-only stage change can re-flow immediately. Activation/resume
    # must first receive a broker acknowledgement: re-flow may auto-execute a
    # reversible candidate action, which cannot precede acceptance of the
    # bootstrap cycle this request promises.
    if skip_assessment_changed and not (
        agent_activated_now or agent_resumed_now
    ):
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )
            reconcile_pending_positive_decisions(
                db,
                role_id=int(role.id),
                expected_role_version=reconcile_control_version,
            )
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed for role_id=%s", role.id
            )
            db.rollback()
    # Activation OR resume kicks the COMPLETE cohort pipeline immediately:
    # enqueue missing scores, reconcile/emit deterministic decisions, then run
    # the LLM only when ambiguous work remains. The old daily-review kick only
    # ran the LLM phase and could leave the scoring backlog untouched until the
    # next scheduled cohort sweep.
    # A broker rejection fails closed: activation is compensated back to OFF
    # (or a resume is re-paused) and the API returns 503. Reporting "on" when
    # no bootstrap was even accepted is worse than asking the user to retry.
    from ...services.workspace_agent_control import workspace_agent_control_snapshot

    workspace_agent_held, _workspace_control_version = (
        workspace_agent_control_snapshot(
            db,
            organization_id=int(current_user.organization_id),
        )
    )
    if (agent_activated_now or agent_resumed_now) and not workspace_agent_held:
        dispatched_role_id = int(role.id)
        dispatched_role_version = int(dispatch_control_version)
        try:
            from ...services.role_agent_dispatch import dispatch_role_agent_cycle

            dispatch_role_agent_cycle(
                role,
                activation=bool(agent_activated_now),
                role_version=dispatched_role_version,
            )
        except Exception:
            logger.exception(
                "Failed to enqueue %s cycle for role_id=%s",
                "activation" if agent_activated_now else "resume",
                dispatched_role_id,
            )
            compensation = compensate_failed_activation_dispatch(
                db,
                role_id=dispatched_role_id,
                organization_id=int(current_user.organization_id),
                dispatched_role_version=dispatched_role_version,
                agent_activated_now=bool(agent_activated_now),
                activation_previous=activation_previous,
                activation_approved_task_id=activation_approved_task_id,
                actor_user_id=int(current_user.id),
                request_id=get_request_id(),
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail=compensation.detail,
            )
        # Toggling the agent on from Settings doesn't run a chat turn, so if the
        # role still carries OLD-engine scores, drop an opt-in re-score offer
        # into its agent chat — the recruiter steers the scope when they open it.
        if agent_activated_now:
            # Interview artifacts are paid role work, so requisition publish
            # leaves them deferred. This kick runs only after the cohort broker
            # accepted activation; the persisted missing-artifact marker is the
            # minute-sweep recovery path.
            on_role_jd_attached(role)
            try:
                from ...tasks.automation_tasks import regenerate_role_tech_questions

                regenerate_role_tech_questions.delay(int(role.id))
            except Exception:
                logger.exception(
                    "tech-question activation kick failed role_id=%s; sweep will retry",
                    role.id,
                )
            try:
                from ...agent_chat import rescore as _rescore
                from ...agent_chat import service as _chat_service

                stale = _rescore.stale_scores_summary(db, role)
                if stale:
                    convo = _chat_service.ensure_conversation(
                        db, organization_id=int(role.organization_id), role=role
                    )
                    _chat_service.post_agent_message(
                        db,
                        conversation=convo,
                        text=(
                            f"I'm on for this role. Heads-up: {stale['stale_count']} candidate"
                            f"{'s' if stale['stale_count'] != 1 else ''} here still have old-engine "
                            f"(v1.x) scores (current scores {stale['score_min']}–{stale['score_max']}). "
                            f"I can re-score them to v2.1.0 — all {stale['stale_count']} for about "
                            f"${stale['est_cost_all_usd']}, or just a subset (say the top 10, or only "
                            "those above/below a score). Want me to, and which?"
                        ),
                    )
                    db.commit()
            except Exception:  # pragma: no cover — heads-up is best-effort
                logger.exception(
                    "stale-scores chat heads-up failed for role_id=%s", role.id
                )
    # Reaching this point means the activation/resume cycle was accepted, or a
    # workspace-level hold intentionally deferred dispatch. Only now may an
    # activation-time stage flip replace and potentially auto-execute cards.
    # A broker failure raises above before any decision/action mutation occurs.
    if skip_assessment_changed and (
        agent_activated_now or agent_resumed_now
    ):
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )

            reconcile_pending_positive_decisions(
                db,
                role_id=int(role.id),
                expected_role_version=reconcile_control_version,
            )
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed for role_id=%s", role.id
            )
            db.rollback()

    # Checklist mutations are part of the acknowledged activation experience,
    # not the pre-dispatch transaction. This also keeps approve-generated-task
    # compensation exact when the broker rejects the bootstrap.
    if agent_activated_now and str(role.role_kind or "") != ROLE_KIND_SISTER:
        try:
            from ...services.agent_activation_checklist import (
                resolve_satisfied_activation_questions,
                surface_activation_questions,
            )

            if activation_approved_task_id is not None:
                resolve_satisfied_activation_questions(db, role=role)
            surface_activation_questions(db, role=role)
            db.commit()
        except Exception:
            logger.exception(
                "Activation checklist failed for role_id=%s", role.id
            )
            db.rollback()
    # When the effective downstream boundary moved, run the same deterministic
    # full-score cohort path used by scheduled agent ticks. It re-evaluates
    # bulk-created cards and decides open scored applications against the new
    # boundary. Stage-1 prescreen cards use their independent calibrated gate
    # and are intentionally untouched. Failures are non-fatal to the saved
    # role edit; the next active cohort tick retries the re-flow.
    if _threshold_may_change:
        try:
            _threshold_after = role_threshold_support.effective_role_fit_threshold(db, role)
        except Exception:
            # Post-update resolution failed — skip reconcile rather than
            # treat the failure as a (None) threshold, which would discard
            # valid numeric-score reject cards.
            logger.exception(
                "Role-fit threshold (post-update) resolution failed for role_id=%s; "
                "skipping decision re-flow", role.id
            )
        else:
            if (
                not role_threshold_support.thresholds_equal(
                    _threshold_before, _threshold_after
                )
                and bool(role.agentic_mode_enabled)
                and role.agent_paused_at is None
            ):
                try:
                    from ...services.bulk_decision_service import decide_role_cohort

                    decide_role_cohort(db, role=role)
                except Exception:
                    logger.exception(
                        "Role-fit threshold re-flow failed for role_id=%s", role.id
                    )
                    db.rollback()
    return role_to_response(role)


# ---------------------------------------------------------------------------
# GET /roles/{role_id}/auto-reject-threshold/suggested
# ---------------------------------------------------------------------------


@router.get("/roles/{role_id}/auto-reject-threshold/suggested")
def suggested_auto_reject_threshold(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the agent's recommended threshold for this role.

    Used by the Agent settings UI when ``auto_reject_threshold_mode`` is
    set to ``auto`` — the recruiter sees the computed value plus a human
    rationale instead of guessing a number.
    """
    from ...services.auto_threshold_service import compute_recommended_threshold

    role = get_role(role_id, current_user.organization_id, db)
    rec = compute_recommended_threshold(db, role=role)
    return rec.to_dict()






@router.put("/roles/{role_id}/job-spec", response_model=RoleJobSpecUpdateResponse)
def update_role_job_spec(
    role_id: int,
    data: RoleJobSpecUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Atomically save the recruiter-authored role spec and assessment tasks.

    The text uses the same deterministic criteria derivation as the role agent.
    Standard roles report re-screen scope without starting paid work; related
    roles reset and queue their alternate-score evaluations after commit.
    """
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    role = (
        db.query(Role)
        .options(selectinload(Role.tasks))
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    assert_role_version(
        role,
        expected_version=int(data.expected_version),
        current_role=lambda: _serialize_role_detail(
            db, role, current_user.organization_id
        ).model_dump(mode="json"),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    audit_before = capture_role_change_snapshot(role)
    audit_from_version = int(role.version or 1)
    is_sister = str(getattr(role, "role_kind", "") or "") == "sister"

    tasks: list[Task] | None = None
    if data.task_ids is not None:
        requested_task_ids = list(
            dict.fromkeys(int(task_id) for task_id in data.task_ids)
        )
        tasks = []
        if requested_task_ids:
            tasks = (
                db.query(Task)
                .filter(
                    Task.id.in_(requested_task_ids),
                    (Task.organization_id == current_user.organization_id)
                    | (Task.organization_id.is_(None)),
                )
                .all()
            )
            tasks_by_id = {int(task.id): task for task in tasks}
            if len(tasks_by_id) != len(requested_task_ids):
                raise HTTPException(
                    status_code=422,
                    detail="One or more selected tasks are unavailable in this organization.",
                )
            tasks = [tasks_by_id[task_id] for task_id in requested_task_ids]

        # Validate the destructive half of an explicit task replacement before
        # changing the spec, criteria, title or relationship. When task_ids is
        # omitted, task configuration is deliberately outside this request and
        # no validation/replacement query should run.
        current_task_ids = {int(task.id) for task in (role.tasks or [])}
        removed_task_ids = current_task_ids - set(requested_task_ids)
        if removed_task_ids:
            used_task = (
                db.query(Assessment.task_id)
                .filter(
                    Assessment.role_id == role.id,
                    Assessment.task_id.in_(removed_task_ids),
                )
                .first()
            )
            if used_task:
                raise HTTPException(
                    status_code=409,
                    detail="A linked task already has assessments and cannot be removed.",
                )

    name = data.name.strip() if data.name is not None else None
    if data.name is not None and not name:
        raise HTTPException(status_code=422, detail="Role name cannot be blank")

    from ...agent_chat.constraints import update_job_spec as apply_job_spec

    try:
        result = apply_job_spec(
            db,
            role,
            job_spec_text=data.job_spec_text,
            provision_assessment_task=not is_sister,
        )
        if not result.get("applied"):
            # The helper has already rolled back if criteria derivation failed.
            raise HTTPException(
                status_code=422 if result.get("ok") is False else 500,
                detail=result.get("error") or "Failed to update job spec",
            )
        if name is not None:
            role.name = name
        if tasks is not None:
            role.tasks = tasks
        # ``apply_job_spec`` owns these fields too so agent-chat edits receive
        # the same truthful override semantics. Assign explicitly here to keep
        # this endpoint's atomic contract obvious and future-proof.
        role.description = (data.job_spec_text or "").strip()
        if role.job_spec_manually_edited_at is None:
            role.job_spec_manually_edited_at = datetime.now(timezone.utc)
        role.interview_focus = None
        role.interview_focus_generated_at = None
        if is_sister:
            result["would_rescreen"] = (
                related_role_spec_lifecycle.mark_related_role_spec_evaluations_stale(
                    db, role
                )
            )
        audit_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
            actor_user_id=int(current_user.id),
            from_version=audit_from_version,
            to_version=audit_to_version,
            request_id=get_request_id(),
            # A task-only save changes the shared job configuration even when
            # all Role columns are identical; preserve its version boundary.
            allow_empty_changes=True,
        )
        db.commit()
        db.refresh(role)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update job spec for role_id=%s", role_id)
        raise HTTPException(status_code=500, detail="Failed to update job spec")

    # Standard-role regeneration is asynchronous and best-effort. Related-role
    # scores remain durably stale until the recruiter explicitly confirms the
    # paid re-score from the roster control.
    if not is_sister:
        try:
            on_role_jd_attached(role)
        except Exception:  # pragma: no cover - persistence must remain successful
            logger.exception(
                "Failed to dispatch interview-focus generation for role_id=%s", role.id
            )

    return {
        "applied": True,
        "role": _serialize_role_detail(db, role, current_user.organization_id),
        "diff": {
            "added": list(result.get("added") or []),
            "removed": list(result.get("removed") or []),
            "criteria_count": int(result.get("criteria_count") or 0),
        },
        "would_rescreen": result.get("would_rescreen") or {
            "count": 0,
            "est_cost_usd": 0.0,
        },
        "scores_invalidated": int(result.get("scores_invalidated") or 0),
        "rescore_dispatch_approved": False,
    }

@router.post("/roles/{role_id}/upload-job-spec")
def upload_role_job_spec(
    role_id: int,
    file: UploadFile = File(...),
    expected_version: int | None = Form(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    # Compatibility for the retired upload clients that predate versions:
    # they may write only the untouched v1 snapshot. As soon as any shared
    # change advances the role, omission fails rather than guessing current.
    if expected_version is None and int(role.version or 1) != 1:
        raise HTTPException(
            status_code=428,
            detail="expected_version is required for a previously changed role",
        )
    assert_role_version(
        role,
        expected_version=int(expected_version or 1),
        current_role=lambda: role_to_response(role).model_dump(mode="json"),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    # Document storage/extraction may be slow. Release the authorization lock
    # while preparing the file, then re-lock and re-check the exact revision
    # before any shared state is changed. A concurrent Turn off or edit is not
    # blocked and wins with a truthful conflict instead of being overwritten.
    db.rollback()
    result = process_document_upload(
        upload=file,
        entity_id=role_id,
        doc_type="job_spec",
        allowed_extensions={"pdf", "docx", "txt"},
    )
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    if expected_version is None and int(role.version or 1) != 1:
        raise HTTPException(
            status_code=428,
            detail="expected_version is required for a previously changed role",
        )
    assert_role_version(
        role,
        expected_version=int(expected_version or 1),
        current_role=lambda: role_to_response(role).model_dump(mode="json"),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    audit_before = capture_role_change_snapshot(role)
    audit_from_version = int(role.version or 1)
    now = datetime.now(timezone.utc)
    role.job_spec_file_url = result["file_url"]
    role.job_spec_filename = result["filename"]
    role.job_spec_text = result["extracted_text"]
    role.description = (result.get("extracted_text") or "").strip() or role.description
    role.job_spec_uploaded_at = now
    role.job_spec_manually_edited_at = now
    role.interview_focus = None
    role.interview_focus_generated_at = None

    is_sister = str(getattr(role, "role_kind", "") or "") == "sister"
    would_rescreen: dict[str, int | float] = {
        "count": 0,
        "est_cost_usd": 0.0,
    }
    try:
        sync_derived_criteria(db, role)
        if is_sister:
            would_rescreen = (
                related_role_spec_lifecycle.mark_related_role_spec_evaluations_stale(
                    db, role
                )
            )
        else:
            mark_role_scores_stale(db, role.id)
            _request_autogenerate_assessment_task(
                role,
                reason="job_spec_upload",
                supersede_generated_drafts=True,
            )
        audit_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
            actor_user_id=int(current_user.id),
            from_version=audit_from_version,
            to_version=audit_to_version,
            reason="job specification uploaded",
            request_id=get_request_id(),
        )
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload job spec")

    # Auto-trigger interview-focus generation in the background. The
    # request returns immediately; the worker writes interview_focus +
    # pack templates back onto the role row when Claude responds.
    if not is_sister:
        on_role_jd_attached(role)
        _maybe_autogenerate_assessment_task(role)

    return {
        "success": True,
        "role_id": role.id,
        "version": int(role.version or 1),
        "filename": result["filename"],
        "text_preview": result["text_preview"],
        "uploaded_at": now,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": not is_sister,
        "would_rescreen": would_rescreen,
        "rescore_dispatch_approved": False,
    }


@router.post("/roles/{role_id}/regenerate-interview-focus")
def regenerate_interview_focus(
    role_id: int,
    data: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regenerate interview focus pointers from the role's job spec. Use after fixing CLAUDE_MODEL."""
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(role, expected_version=data.expected_version)
    role.interview_focus = None
    role.interview_focus_generated_at = None
    _add_role_change_boundary(
        db,
        role=role,
        current_user=current_user,
        action="interview_focus_regeneration_requested",
        reason="interview focus regeneration requested",
    )

    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to regenerate interview focus")

    on_role_jd_attached(role)

    return {
        "success": True,
        "role_id": role.id,
        "version": int(role.version or 1),
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": True,
    }


# Keep the historical import surface while each cohesive route group owns its
# own router. Including them here preserves the public API assembled by
# assessments_runtime.routes.
from .role_criteria_routes import (  # noqa: E402
    _INVALIDATING_BUCKETS as _INVALIDATING_BUCKETS,
    _commit_role_criterion_change as _commit_role_criterion_change,
    _get_role_criterion as _get_role_criterion,
    _next_role_criterion_ordering as _next_role_criterion_ordering,
    create_role_criterion as create_role_criterion,
    delete_role_criterion as delete_role_criterion,
    reset_role_criteria_to_workspace as reset_role_criteria_to_workspace,
    router as role_criteria_router,
    sync_role_criteria_with_workspace as sync_role_criteria_with_workspace,
    update_role_criterion as update_role_criterion,
)
from .role_lifecycle_routes import (  # noqa: E402
    delete_role as delete_role,
    router as role_lifecycle_router,
    star_role as star_role,
    unstar_role as unstar_role,
)
from .role_task_feedback_routes import (  # noqa: E402
    _serialize_feedback_note as _serialize_feedback_note,
    add_role_task as add_role_task,
    create_role_feedback_note as create_role_feedback_note,
    list_role_feedback_notes as list_role_feedback_notes,
    list_role_tasks as list_role_tasks,
    remove_role_task as remove_role_task,
    router as role_task_feedback_router,
)

router.include_router(role_criteria_router)
router.include_router(role_lifecycle_router)
router.include_router(role_task_feedback_router)
