from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ...models.organization import Organization
from ...models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from ...models.role_brief import RoleBrief
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...models.org_criterion import (
    BUCKET_PREFERRED,
    CRITERION_BUCKETS,
    OrganizationCriterion,
)
from ...models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
    RoleCriterion,
)
from ...schemas.role import (
    JobStatusUpdate,
    RoleClientUpdate,
    RoleCreate,
    RoleCriterionCreate,
    RoleCriterionResponse,
    RoleCriterionUpdate,
    RoleFeedbackNoteCreate,
    RoleFeedbackNoteResponse,
    RoleJobSpecUpdate,
    RoleJobSpecUpdateResponse,
    RoleResponse,
    RoleTaskLinkRequest,
    RoleUpdate,
)
from ...services.application_events import on_role_jd_attached
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
    reset_role_to_workspace,
    sync_all_criteria,
    sync_derived_criteria,
    sync_role_with_workspace,
)
from .role_support import get_role, role_to_response
from .pipeline_service import role_pipeline_counts, role_pipeline_counts_bulk
from ..agentic._hub_shared import role_pending_decisions_by_type

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")


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


def _request_autogenerate_assessment_task(
    role,
    *,
    reason: str,
    supersede_generated_drafts: bool = False,
    defer_until_activation: bool = False,
) -> bool:
    """Stamp durable generation intent in the caller-owned transaction."""
    from ...platform.config import settings

    if not getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
        return False
    from ...services.task_provisioning_service import (
        request_assessment_task_provisioning,
    )

    return request_assessment_task_provisioning(
        role,
        reason=reason,
        supersede_generated_drafts=supersede_generated_drafts,
        defer_until_activation=defer_until_activation,
    )


def _maybe_autogenerate_assessment_task(role) -> None:
    """Kick draft-task generation after the durable intent has committed.

    This defaults on so role creation owns task authoring; the generated task
    remains inactive until the necessary human content approval. A kick failure
    is non-fatal because the persisted request is recovered by the Beat sweep.
    """
    try:
        from ...platform.config import settings
        if not getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
            return
        from ...services.task_provisioning_service import (
            PROVISIONING_RECOVERABLE_STATUSES,
            task_provisioning_state,
        )

        state = task_provisioning_state(role)
        if state and str(state.get("status") or "") not in PROVISIONING_RECOVERABLE_STATUSES:
            return
        from ...tasks.assessment_tasks import generate_assessment_task_for_role
        generate_assessment_task_for_role.delay(int(role.id), int(role.organization_id))
    except Exception:  # pragma: no cover — provisioning must never break role create
        import logging
        logging.getLogger("taali.roles").warning(
            "auto-generate enqueue failed for role %s", getattr(role, "id", "?"), exc_info=True
        )


@router.get("/roles")
def list_roles(
    include_pipeline_stats: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Ordering: starred roles always on top (active auto-sync roles need
    # to surface first), then by the most recently updated — which for
    # Workable-sourced roles tracks the last sync that touched the row.
    # ``created_at`` is the final tie-breaker so newly-created roles win
    # over older roles that have never been updated.
    roles_query = (
        db.query(Role)
        # selectinload tasks for the per-role task count. Criteria are NOT
        # loaded here: the list serializes with summary=True (see below), which
        # drops the criteria array entirely, so hydrating it would only transfer
        # rows we discard. selectin (not joined) keeps ``.limit()`` below
        # applying cleanly to roles rather than to a tasks cartesian product.
        .options(
            selectinload(Role.tasks),
            joinedload(Role.ats_owner_role),
            selectinload(Role.sister_roles),
        )
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(
            Role.starred_for_auto_sync.desc(),
            Role.updated_at.desc().nullslast(),
            Role.created_at.desc(),
        )
    )
    # Progressive load: the Jobs hub fetches a first page (``limit``) to paint
    # the active / most-recent roles instantly, then re-fetches the full list
    # in the background. The sort above front-loads starred + recently-synced
    # roles, so page one is the set a recruiter actually works. ``limit`` also
    # scopes every per-role aggregate below to the page (fewer role_ids → the
    # candidate_applications scans shrink), so the first paint is cheap too.
    if limit is not None:
        roles_query = roles_query.limit(limit)
    roles = roles_query.all()
    if not roles:
        return []

    role_ids = [role.id for role in roles]
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
        and role_accepts_native_applications(role)
    }

    return [
        role_to_response(
            role,
            summary=True,
            tasks_count=len(role.tasks or []),
            applications_count=app_counts.get(int(role.ats_owner_role_id or role.id), 0),
            stage_counts=stage_counts_by_role.get(int(role.ats_owner_role_id or role.id), {}),
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
    stage_counts = role_pipeline_counts(
        db, organization_id=organization_id, role_id=operational_role_id
    )
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
        and role_accepts_native_applications(role)
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
    query = db.query(Role).options(joinedload(Role.ats_owner_role))
    if not shell:
        query = query.options(
            joinedload(Role.tasks),
            selectinload(Role.sister_roles),
        )
    role = (
        query
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if shell:
        # Deliberately one bounded role query and no candidate/decision scans.
        # The SPA uses this to paint the header and navigation immediately,
        # then requests the authoritative aggregates in the background.
        return role_to_response(
            role,
            summary=True,
            tasks_count=0,
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
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
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
    role.job_status = data.status
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

    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    set_role_client(
        db,
        organization_id=current_user.organization_id,
        role_id=role.id,
        client_id=data.client_id,
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


def _effective_pre_screen_threshold(db: Session, role: Role) -> float | None:
    """The 0-100 cutoff the deterministic pre-screen reject actually uses
    for this role — the same value ``resolved_auto_reject_config`` feeds the
    auto-reject decider (``score_threshold`` in manual mode, the computed
    value in auto mode). ``org`` isn't needed for the threshold itself, so
    we pass None to avoid an extra load.

    Raises on failure (does NOT swallow to ``None``): a resolution error —
    e.g. while switching to ``auto`` mode — must not be mistaken for a
    genuine "no threshold" value, or the reconcile would treat every
    numeric-score reject as no-longer-below-threshold and discard it.
    """
    from ...services.pre_screening_service import resolved_auto_reject_config

    return resolved_auto_reject_config(None, role, db=db)["threshold_100"]


def _thresholds_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.05


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    updates = data.model_dump(exclude_unset=True)
    # Command-only field: never persist it as Role state. It exists so the
    # necessary candidate-content HITL decision can compose with the one
    # Turn-on mutation instead of becoming a separate setup workflow.
    activation_assessment_action = updates.pop(
        "activation_assessment_action", None
    )
    if role_is_score_only(role):
        unsafe_automation = {
            key
            for key in (
                "agentic_mode_enabled",
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
    if (
        updates.get("auto_skip_assessment") is False
        and bool(role.agentic_mode_enabled)
        and not any(bool(task.is_active) for task in (role.tasks or []))
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Assign an active assessment task before turning assessment "
                "skipping off. This candidate-facing workflow change requires "
                "an explicit task choice."
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

        activation_policy = activation_policy_values(role, updates)
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
            if not list(role.tasks or []):
                from ...tasks.assessment_tasks import generate_assessment_task_for_role

                generate_assessment_task_for_role.delay(
                    int(role.id), int(role.organization_id)
                )
            elif activation_intent_task_ready(role):
                from ...tasks.agent_tasks import agent_cohort_tick_role

                agent_cohort_tick_role.delay(
                    int(role.id),
                    activation=True,
                    activation_intent_id=str(intent["request_id"]),
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
    # A pre-screen threshold change (the per-role override or the
    # manual/auto mode) moves the deterministic reject verdict for every
    # candidate without touching any score. Snapshot the *effective*
    # threshold before mutating so we can tell afterwards whether it
    # actually moved and, if so, reconcile the reject queue (below).
    _threshold_may_change = (
        "score_threshold" in updates or "auto_reject_threshold_mode" in updates
    )
    _threshold_before = None
    if _threshold_may_change:
        try:
            _threshold_before = _effective_pre_screen_threshold(db, role)
        except Exception:
            # No safe baseline to compare against → don't reconcile (and
            # never block the role edit itself on a threshold-resolution error).
            logger.exception(
                "Pre-screen threshold (pre-update) resolution failed for role_id=%s", role.id
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
    activation_previous = {
        "agentic_mode_enabled": bool(role.agentic_mode_enabled),
        "agent_paused_at": role.agent_paused_at,
        "agent_paused_reason": role.agent_paused_reason,
        "auto_promote": bool(role.auto_promote),
        "auto_send_assessment": getattr(role, "auto_send_assessment", None),
        "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
        "auto_advance": getattr(role, "auto_advance", None),
        "starred_for_auto_sync": bool(role.starred_for_auto_sync),
        "job_status": role.job_status,
    }
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
                # Feed the effective path into both readiness below and the
                # normal field assignment later in this transaction.
                updates["auto_skip_assessment"] = True
            # The Turn-on command itself is the recruiter's authorization to
            # use the one generated task that has already passed the automated
            # sandbox battle test. Requiring a second "approve" click after
            # Turn on added ceremony without adding a distinct safety choice.
            # Keep the explicit command supported for older clients, but make
            # the API's normal one-switch contract work on its own as well.
            if (
                activation_assessment_action is None
                and not was_enabled
                and not bool(
                    updates.get(
                        "auto_skip_assessment",
                        getattr(role, "auto_skip_assessment", False),
                    )
                )
                and not any(bool(task.is_active) for task in (role.tasks or []))
            ):
                generated_drafts = []
                for task in list(role.tasks or []):
                    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
                    if (
                        not bool(task.is_active)
                        and extra.get("generated")
                        and extra.get("needs_review", True)
                        and (extra.get("battle_test") or {}).get("verdict") == "pass"
                    ):
                        generated_drafts.append(task)
                if len(generated_drafts) == 1:
                    activation_assessment_action = "approve_generated_task"

            if activation_assessment_action == "approve_generated_task":
                drafts = []
                for task in list(role.tasks or []):
                    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
                    if (
                        not bool(task.is_active)
                        and extra.get("generated")
                        and extra.get("needs_review", True)
                    ):
                        drafts.append(task)
                if len(drafts) != 1:
                    db.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Turn on can approve exactly one linked generated "
                            "draft; wait for generation or resolve multiple "
                            "drafts before retrying."
                        ),
                    )
                draft = drafts[0]
                extra = draft.extra_data if isinstance(draft.extra_data, dict) else {}
                verdict = (extra.get("battle_test") or {}).get("verdict")
                if verdict != "pass":
                    db.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The generated assessment cannot be approved until "
                            "its automated battle test passes. Choose Skip "
                            "assessment or retry after validation completes."
                        ),
                    )
                try:
                    from ...services.task_approval_service import (
                        TaskApprovalError,
                        approve_task_for_use,
                    )

                    approve_task_for_use(
                        db,
                        draft,
                        user_id=int(current_user.id),
                    )
                except TaskApprovalError as exc:
                    db.rollback()
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "The generated assessment repository is not ready; "
                            f"Turn on was not applied: {exc}"
                        ),
                    ) from exc
            # Production activation is a contract, not a hopeful toggle: verify
            # the live worker plus the external dependencies this role's path
            # actually uses before changing any state.
            from ...services.agent_activation_readiness import (
                activation_readiness,
                readiness_message,
            )

            # Evaluate the effective path from this PATCH, not only the stale
            # persisted value.  Enabling + skipping assessments atomically must
            # not require email/repository providers the role will not use.
            activation_policy = activation_policy_values(role, updates)
            readiness = activation_readiness(
                role,
                auto_skip_assessment=(
                    bool(updates["auto_skip_assessment"])
                    if updates.get("auto_skip_assessment") is not None
                    else None
                ),
                monthly_usd_budget_cents=int(incoming_budget),
                auto_send_assessment=activation_policy[
                    "auto_send_assessment"
                ],
                auto_resend_assessment=activation_policy[
                    "auto_resend_assessment"
                ],
                auto_advance=activation_policy["auto_advance"],
                auto_reject=(
                    bool(updates["auto_reject"])
                    if updates.get("auto_reject") is not None
                    else None
                ),
                auto_reject_pre_screen=(
                    bool(updates["auto_reject_pre_screen"])
                    if updates.get("auto_reject_pre_screen") is not None
                    else None
                ),
            )
            if not readiness.get("ready"):
                db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Agent runtime is not ready: "
                        f"{readiness_message(readiness)}. Turn on was not applied."
                    ),
                )
        role.agentic_mode_enabled = next_enabled
        agent_activated_now = next_enabled and not was_enabled
        # Snapshot each reversible-action choice at activation. Concrete role
        # settings survive Turn on; a truly legacy role with no granular values
        # retains the historical one-switch default (all three on).
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
    if agent_activated_now or agent_resumed_now:
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
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")
    # An assessment-stage flip must re-flow already-pending send/advance
    # cards right away — otherwise a skip-toggled role still has assessment
    # invites sitting in the Decision Hub for one-click approval (Codex #866).
    # Best-effort: the save already committed; a reconcile failure only means
    # the next cohort tick converts them instead.
    if skip_assessment_changed:
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )
            reconcile_pending_positive_decisions(db, role=role)
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed for role_id=%s", role.id
            )
            db.rollback()
    # On activation, surface every missing-config gap as a NeedsInput row
    # on the Home hub in one shot — the recruiter sees the full checklist
    # rather than discovering gaps one cycle at a time. Idempotent on
    # (role_id, kind). Fires every false→true transition regardless of
    # whether the role was previously active.
    if agent_activated_now:
        try:
            from ...services.agent_activation_checklist import surface_activation_questions
            surface_activation_questions(db, role=role)
            db.commit()
        except Exception:
            logger.exception(
                "Activation checklist failed for role_id=%s", role.id
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
    if agent_activated_now or agent_resumed_now:
        try:
            from ...tasks.agent_tasks import agent_cohort_tick_role
            agent_cohort_tick_role.delay(
                int(role.id), activation=bool(agent_activated_now)
            )
        except Exception:
            logger.exception(
                "Failed to enqueue %s cycle for role_id=%s",
                "activation" if agent_activated_now else "resume",
                role.id,
            )
            if agent_activated_now:
                # Compensate the entire activation contract, not only the
                # toggle.  Otherwise a broker outage could leave a native job
                # publicly OPEN and the role auto-promoting/starred while the
                # agent itself is OFF.
                role.agentic_mode_enabled = activation_previous[
                    "agentic_mode_enabled"
                ]
                role.agent_paused_at = activation_previous["agent_paused_at"]
                role.agent_paused_reason = activation_previous[
                    "agent_paused_reason"
                ]
                role.auto_promote = activation_previous["auto_promote"]
                for field in GRANULAR_AUTOMATION_FIELDS:
                    setattr(role, field, activation_previous[field])
                role.starred_for_auto_sync = activation_previous[
                    "starred_for_auto_sync"
                ]
                role.job_status = activation_previous["job_status"]
            else:
                from ...agent_runtime import budget_guard as _budget_guard

                _budget_guard.pause_role(
                    db, role=role, reason="agent bootstrap dispatch failed"
                )
            role.agent_bootstrap_status = "failed"
            role.agent_bootstrap_error = "agent bootstrap dispatch failed"
            role.agent_bootstrap_completed_at = datetime.now(timezone.utc)
            db.commit()
            raise HTTPException(
                status_code=503,
                detail=(
                    "The agent could not be started because the worker queue is "
                    "unavailable. It was left off/paused; retry Turn on."
                ),
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
    # When the effective pre-screen threshold actually moved, re-align the
    # deterministic skip_assessment_reject queue so the Decision Hub, the
    # role's pending count, and the "below threshold" stat all agree with
    # the new cutoff. No re-scoring — scores are unchanged; only the
    # verdict moves (contrast mark_role_scores_stale for criteria/job-spec
    # edits, which DO change scores). Failures are logged, never fatal to
    # the PATCH.
    if _threshold_may_change:
        try:
            _threshold_after = _effective_pre_screen_threshold(db, role)
        except Exception:
            # Post-update resolution failed — skip reconcile rather than
            # treat the failure as a (None) threshold, which would discard
            # valid numeric-score reject cards.
            logger.exception(
                "Pre-screen threshold (post-update) resolution failed for role_id=%s; "
                "skipping reject reconcile", role.id
            )
        else:
            if not _thresholds_equal(_threshold_before, _threshold_after):
                try:
                    from ...services.pre_screen_decision_emitter import (
                        reconcile_pre_screen_reject_decisions,
                        retract_advances_below_threshold,
                    )
                    # Order matters: retract stale advances FIRST so the reject
                    # reconcile's emit loop (which skips apps that already have a
                    # pending decision) can replace each with the correct
                    # skip_assessment_reject card.
                    retract_advances_below_threshold(
                        db,
                        role=role,
                        organization_id=int(current_user.organization_id),
                        threshold=_threshold_after,
                    )
                    reconcile_pre_screen_reject_decisions(
                        db,
                        role=role,
                        organization_id=int(current_user.organization_id),
                        threshold=_threshold_after,
                    )
                    db.commit()
                except Exception:
                    logger.exception(
                        "Pre-screen threshold re-apply failed for role_id=%s", role.id
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


# ---------------------------------------------------------------------------
# Per-role criteria — chip CRUD, sync, reset
# ---------------------------------------------------------------------------


def _get_role_criterion(
    db: Session, role: Role, criterion_id: int
) -> RoleCriterion:
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
    role = get_role(role_id, current_user.organization_id, db)
    bucket = data.bucket or BUCKET_PREFERRED
    if bucket not in CRITERION_BUCKETS:
        raise HTTPException(status_code=422, detail="Invalid bucket")
    chip = RoleCriterion(
        role_id=role.id,
        source=CRITERION_SOURCE_RECRUITER,
        ordering=int(data.ordering) if data.ordering is not None else _next_role_criterion_ordering(db, role),
        weight=float(data.weight) if data.weight is not None else 1.0,
        must_have=(bucket == "must"),
        bucket=bucket,
        org_criterion_id=None,
        text=data.text.strip(),
    )
    db.add(chip)
    _commit_role_criterion_change(
        db, role, invalidate_scores=bucket in _INVALIDATING_BUCKETS,
    )
    db.refresh(chip)
    return chip


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
    role = get_role(role_id, current_user.organization_id, db)
    chip = _get_role_criterion(db, role, criterion_id)
    updates = data.model_dump(exclude_unset=True)
    old_bucket = chip.bucket
    text_changed = "text" in updates and updates["text"] is not None and updates["text"].strip() != (chip.text or "")
    bucket_changed = "bucket" in updates and updates["bucket"] is not None and updates["bucket"] != chip.bucket
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
    _commit_role_criterion_change(db, role, invalidate_scores=needs_invalidation)
    db.refresh(chip)
    return chip


@router.delete(
    "/roles/{role_id}/criteria/{criterion_id}",
    status_code=204,
)
def delete_role_criterion(
    role_id: int,
    criterion_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
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
        db, role, invalidate_scores=old_bucket in _INVALIDATING_BUCKETS,
    )
    return None


@router.post("/roles/{role_id}/criteria/sync", response_model=RoleResponse)
def sync_role_criteria_with_workspace(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-apply workspace text + bucket on non-customized, non-suppressed
    role chips, add any newly-introduced workspace chips, drop the
    workspace link on chips whose workspace counterpart is gone."""
    role = get_role(role_id, current_user.organization_id, db)
    sync_role_with_workspace(db, role)
    _commit_role_criterion_change(db, role)
    db.refresh(role)
    return role_to_response(role)


@router.post("/roles/{role_id}/criteria/reset", response_model=RoleResponse)
def reset_role_criteria_to_workspace(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hard-delete every recruiter chip on this role and re-snapshot
    workspace defaults. Suppressions are cleared. ``derived_from_spec``
    chips are untouched."""
    role = get_role(role_id, current_user.organization_id, db)
    reset_role_to_workspace(db, role)
    _commit_role_criterion_change(db, role)
    db.refresh(role)
    return role_to_response(role)


@router.post("/roles/{role_id}/star", response_model=RoleResponse)
def star_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a role as starred for auto-sync + real-time scoring.

    Side-effect: kick off an immediate Workable sync filtered to this role
    so the recruiter sees fresh candidates within seconds rather than
    waiting up to 15 min for the next Beat tick. Skipped silently for
    manual roles (no workable_job_id) or when another sync is already
    running for the org.
    """
    role = get_role(role_id, current_user.organization_id, db)
    role.starred_for_auto_sync = True
    # A manual star is sticky — it must survive Workable state changes, so it
    # is never flagged auto-managed (only the published-state automation sets
    # that flag, and only it removes such stars).
    role.star_auto_managed = False
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to star role")

    if (role.source == "workable") and (role.workable_job_id or "").strip():
        try:
            from ..workable_sync.routes import kick_off_filtered_sync

            org = (
                db.query(Organization)
                .filter(Organization.id == current_user.organization_id)
                .first()
            )
            if org is not None:
                kick_off_filtered_sync(
                    db,
                    org=org,
                    job_shortcodes=[str(role.workable_job_id).strip()],
                    requested_by_user_id=current_user.id,
                    mode="full",
                )
        except Exception:
            logger.exception(
                "Failed to kick off immediate sync after starring role_id=%s",
                role.id,
            )

    return role_to_response(role)


@router.delete("/roles/{role_id}/star", response_model=RoleResponse)
def unstar_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    # Live (published) roles are always kept in continuous sync — ignore
    # attempts to unstar them. The next jobs-only sync would re-star them
    # anyway; refusing here avoids a confusing flicker and keeps the
    # invariant server-side.
    job_state = ""
    if isinstance(role.workable_job_data, dict):
        job_state = str(role.workable_job_data.get("state") or "").strip().lower()
    if job_state == "published":
        return role_to_response(role)
    role.starred_for_auto_sync = False
    role.star_auto_managed = False
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unstar role")
    return role_to_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    has_applications = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == current_user.organization_id,
        CandidateApplication.role_id == role.id,
    ).first()
    if has_applications:
        raise HTTPException(status_code=400, detail="Cannot delete role with applications")
    in_use = db.query(Assessment).filter(
        Assessment.organization_id == current_user.organization_id,
        Assessment.role_id == role.id,
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Cannot delete role with assessments")
    try:
        db.delete(role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete role")
    return None


@router.put("/roles/{role_id}/job-spec", response_model=RoleJobSpecUpdateResponse)
def update_role_job_spec(
    role_id: int,
    data: RoleJobSpecUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Atomically save the recruiter-authored role spec and assessment tasks.

    The text is applied through the same deterministic criteria derivation used
    by the role agent. This reports the candidate re-screen scope and cost but
    deliberately does not start paid scoring work.
    """
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
    if str(getattr(role, "role_kind", "") or "") == "sister":
        raise HTTPException(
            status_code=409,
            detail="Sister roles are score-only views. Edit the original ATS role's job spec.",
        )

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
        result = apply_job_spec(db, role, job_spec_text=data.job_spec_text)
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
        db.commit()
        db.refresh(role)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update job spec for role_id=%s", role_id)
        raise HTTPException(status_code=500, detail="Failed to update job spec")

    # Regeneration is asynchronous and best-effort. The recruiter-authored spec
    # is already durable even if the worker/broker is temporarily unavailable.
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
    }


@router.post("/roles/{role_id}/upload-job-spec")
def upload_role_job_spec(
    role_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    result = process_document_upload(
        upload=file,
        entity_id=role_id,
        doc_type="job_spec",
        allowed_extensions={"pdf", "docx", "txt"},
    )
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
    try:
        sync_derived_criteria(db, role)
        if is_sister:
            from ...services.sister_role_service import ensure_sister_evaluations

            ensure_sister_evaluations(db, role, reset_existing=True)
        else:
            mark_role_scores_stale(db, role.id)
            _request_autogenerate_assessment_task(
                role,
                reason="job_spec_upload",
                supersede_generated_drafts=True,
            )
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload job spec")

    # Auto-trigger interview-focus generation in the background. The
    # request returns immediately; the worker writes interview_focus +
    # pack templates back onto the role row when Claude responds.
    if is_sister:
        from ...tasks.sister_role_tasks import score_sister_role

        try:
            score_sister_role.apply_async(args=[role.id], queue="scoring")
        except Exception as exc:  # Beat recovers the committed pending rows.
            logger.error(
                "Related-role spec scoring kick unavailable role_id=%s "
                "error_code=queue_unavailable error_type=%s",
                role.id,
                type(exc).__name__,
            )
    else:
        on_role_jd_attached(role)
        _maybe_autogenerate_assessment_task(role)

    return {
        "success": True,
        "role_id": role.id,
        "filename": result["filename"],
        "text_preview": result["text_preview"],
        "uploaded_at": now,
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": True,
    }


@router.post("/roles/{role_id}/regenerate-interview-focus")
def regenerate_interview_focus(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regenerate interview focus pointers from the role's job spec. Use after fixing CLAUDE_MODEL."""
    role = get_role(role_id, current_user.organization_id, db)
    role.interview_focus = None
    role.interview_focus_generated_at = None

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
        "interview_focus_generated": bool(role.interview_focus),
        "interview_focus_generated_at": role.interview_focus_generated_at,
        "interview_focus": role.interview_focus,
        "interview_focus_pending": True,
    }


@router.get("/roles/{role_id}/tasks")
def list_role_tasks(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    from ...services.task_battle_test import battle_test_summary

    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "scenario": t.scenario,
            "difficulty": t.difficulty,
            "duration_minutes": t.duration_minutes,
            "task_type": t.task_type,
            "is_active": bool(t.is_active),
            "generated": bool(
                isinstance(t.extra_data, dict) and t.extra_data.get("generated")
            ),
            "needs_review": bool(
                isinstance(t.extra_data, dict) and t.extra_data.get("needs_review")
            ),
            "battle_test": (
                battle_test_summary(t)
                if isinstance(t.extra_data, dict) and t.extra_data.get("generated")
                else None
            ),
        }
        for t in (role.tasks or [])
    ]


@router.post("/roles/{role_id}/tasks")
def add_role_task(
    role_id: int,
    data: RoleTaskLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    task = db.query(Task).filter(
        Task.id == data.task_id,
        (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not any(t.id == task.id for t in (role.tasks or [])):
        role.tasks.append(task)
    try:
        # Linking an already-active task fills the activation gap immediately;
        # an inactive generated draft intentionally leaves the prompt open
        # until the shared approval service activates it.
        db.flush()
        from ...services.agent_activation_checklist import (
            resolve_satisfied_activation_questions,
        )

        resolve_satisfied_activation_questions(db, role=role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to link task to role")
    return {"success": True, "role_id": role.id, "task_id": task.id}


@router.delete("/roles/{role_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role_task(
    role_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    in_use = db.query(Assessment).filter(
        Assessment.organization_id == current_user.organization_id,
        Assessment.role_id == role.id,
        Assessment.task_id == task_id,
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Cannot unlink task that already has assessments")
    role.tasks = [t for t in (role.tasks or []) if t.id != task_id]
    enabled_last_task_removed = bool(
        role.agentic_mode_enabled
        and not any(bool(task.is_active) for task in (role.tasks or []))
        and not bool(role.auto_skip_assessment)
    )
    if enabled_last_task_removed:
        # Choosing "No assessment task" is the recruiter's explicit choice to
        # bypass that stage. Keep the live role internally consistent instead
        # of silently translating taskless send decisions into advances while
        # settings still claim assessment skipping is off.
        role.auto_skip_assessment = True
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unlink task from role")
    if enabled_last_task_removed:
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )

            reconcile_pending_positive_decisions(db, role=role)
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed after task unlink role_id=%s",
                role.id,
            )
            db.rollback()
    return None


# ---------------------------------------------------------------------------
# Recruiter feedback notes — freeform observations about agent behaviour on
# this role. Append-only timeline; the most-recent N rows are inlined into
# the agent's system prompt by ``system_prompt._render_recruiter_feedback_notes``
# so the agent picks the feedback up on the next cycle.
# ---------------------------------------------------------------------------


def _serialize_feedback_note(row) -> dict:
    author = row.author
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "author_user_id": int(row.author_user_id) if row.author_user_id else None,
        "author_name": (
            (author.full_name if getattr(author, "full_name", None) else author.email)
            if author
            else None
        ),
        "note": row.note,
        "created_at": row.created_at,
    }


@router.get(
    "/roles/{role_id}/feedback-notes",
    response_model=list[RoleFeedbackNoteResponse],
)
def list_role_feedback_notes(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ...agent_runtime.role_feedback_notes import list_notes

    role = get_role(role_id, current_user.organization_id, db)
    rows = list_notes(db, role_id=role.id, limit=200)
    return [_serialize_feedback_note(r) for r in rows]


@router.post(
    "/roles/{role_id}/feedback-notes",
    response_model=RoleFeedbackNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_role_feedback_note(
    role_id: int,
    data: RoleFeedbackNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ...agent_runtime.role_feedback_notes import create_note

    role = get_role(role_id, current_user.organization_id, db)
    try:
        row = create_note(
            db,
            organization_id=int(current_user.organization_id),
            role_id=int(role.id),
            note=data.note,
            author_user_id=int(current_user.id),
        )
        db.commit()
        db.refresh(row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Failed to create role feedback note for role_id=%s", role_id)
        raise HTTPException(status_code=500, detail="Failed to create feedback note")
    return _serialize_feedback_note(row)
