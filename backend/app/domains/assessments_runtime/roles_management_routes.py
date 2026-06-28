from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
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
    RoleResponse,
    RoleTaskLinkRequest,
    RoleUpdate,
)
from ...services.application_events import on_role_jd_attached
from ...services.document_service import process_document_upload
from ...services.cv_score_orchestrator import mark_role_scores_stale
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
    monthly_budget_cents = data.monthly_usd_budget_cents
    if monthly_budget_cents is None and org is not None:
        org_budget = getattr(org, "default_role_budget_cents", None)
        if org_budget is not None:
            monthly_budget_cents = max(0, int(org_budget))

    score_threshold = data.score_threshold
    if score_threshold is None and org is not None:
        org_threshold = getattr(org, "default_score_threshold", None)
        if org_threshold is not None:
            score_threshold = max(0, min(100, int(org_threshold)))

    role = Role(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        description=(data.description or None),
        screening_pack_template=(data.screening_pack_template.model_dump() if data.screening_pack_template else None),
        tech_interview_pack_template=(data.tech_interview_pack_template.model_dump() if data.tech_interview_pack_template else None),
        workable_actor_member_id=(data.workable_actor_member_id or None),
        monthly_usd_budget_cents=monthly_budget_cents,
        score_threshold=score_threshold,
    )
    db.add(role)
    try:
        db.flush()
        sync_all_criteria(db, role)
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create role")

    # Auto-provision a draft assessment task from the role's JD (gated;
    # default off). Off-request via Celery — generation is slow + paid.
    _maybe_autogenerate_assessment_task(role)
    return role_to_response(role)


def _maybe_autogenerate_assessment_task(role) -> None:
    """Enqueue draft-task generation for a new role when the org has
    opted in (``AUTO_GENERATE_ASSESSMENT_TASKS``). Best-effort; never
    blocks role creation."""
    try:
        from ...platform.config import settings
        if not getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
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
        # selectinload both collections: without it, role_to_response
        # lazy-loads ``role.criteria`` once PER role — an N+1 that, on an org
        # with ~100 roles, fired ~100 extra sequential queries and dominated
        # /roles latency (107 → 8 queries with this). selectin (not joined)
        # also keeps ``.limit()`` below applying cleanly to roles rather than
        # to a tasks/criteria cartesian product.
        .options(selectinload(Role.tasks), selectinload(Role.criteria))
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
    app_counts_rows = (
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.role_id.in_(role_ids),
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
                CandidateApplication.role_id.in_(role_ids),
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
                CandidateApplication.role_id.in_(role_ids),
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
            role_ids=role_ids,
        )

    # Batched role -> client lookup (one query) for the Jobs list's Client column
    # + filter. Roles with no requisition (or no client) are simply absent.
    from ...services.role_brief_service import role_client_map

    clients_by_role = role_client_map(
        db, organization_id=current_user.organization_id, role_ids=role_ids
    )

    return [
        role_to_response(
            role,
            tasks_count=len(role.tasks or []),
            applications_count=app_counts.get(role.id, 0),
            stage_counts=stage_counts_by_role.get(role.id, {}),
            active_candidates_count=active_counts.get(role.id, 0),
            last_candidate_activity_at=last_activity_by_role.get(role.id),
            client=clients_by_role.get(role.id),
        )
        for role in roles
    ]


def _serialize_role_detail(db: Session, role: Role, organization_id: int) -> RoleResponse:
    """The full role-detail payload: funnel counts + pending-decision chips + the
    linked requisition's structured spec. Shared by GET /roles/{id} and the
    job-status mutation so both stay in lock-step."""
    app_count = (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role.id,
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
        db, organization_id=organization_id, role_id=role.id
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
    return role_to_response(
        role,
        tasks_count=len(role.tasks or []),
        applications_count=int(app_count),
        stage_counts=stage_counts,
        pending_decisions_by_type=pending_decisions_by_type,
        requisition=requisition,
        client=client,
    )


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role_endpoint(
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
    return _serialize_role_detail(db, role, current_user.organization_id)


@router.post("/roles/{role_id}/job-status", response_model=RoleResponse)
def set_job_status_endpoint(
    role_id: int,
    data: JobStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the job's lifecycle status (draft / open / filled / filled_external /
    cancelled). The recruiter is the authority — any valid status is accepted,
    including reopening or marking a role filled by an outside vendor. ``reason``
    is recorded on the role's feedback timeline for the audit trail."""
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
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
    if "agentic_mode_enabled" in updates:
        next_enabled = bool(updates["agentic_mode_enabled"])
        was_enabled = bool(role.agentic_mode_enabled)
        was_paused = role.agent_paused_at is not None
        if next_enabled:
            # Activating requires a monthly USD budget so the agent can't
            # quietly run up costs. Allow either a value already on the role
            # or one supplied in the same PATCH.
            incoming_budget = updates.get("monthly_usd_budget_cents", role.monthly_usd_budget_cents)
            if incoming_budget is None or int(incoming_budget) <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="monthly_usd_budget_cents is required to enable agentic mode",
                )
        role.agentic_mode_enabled = next_enabled
        # Re-enabling clears any prior pause so the next event can run.
        if role.agentic_mode_enabled and role.agent_paused_at is not None:
            role.agent_paused_at = None
            role.agent_paused_reason = None
        agent_activated_now = next_enabled and not was_enabled
        # Agent-on implies "auto-sync this role" — keep the periodic
        # Workable fetch (comments, activities, questionnaire answers)
        # flowing so the agent's pre-screen + scoring see fresh signal
        # without the recruiter having to remember to star it. One-way:
        # disabling the agent doesn't unstar (star is sticky).
        if agent_activated_now and not role.starred_for_auto_sync:
            role.starred_for_auto_sync = True
        # Resume = "was paused while enabled, now no longer paused while still
        # enabled". Distinct from activation. Both deserve an immediate cycle —
        # otherwise the recruiter clicks Resume and waits up to 30 minutes for
        # the next beat-scheduled tick to fire.
        agent_resumed_now = (
            was_enabled
            and was_paused
            and next_enabled
            and role.agent_paused_at is None
        )
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
        from ...agent_runtime import budget_guard

        if budget_guard.resume_if_under_budget(db, role=role):
            agent_resumed_now = True
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
    if "auto_promote" in updates and updates["auto_promote"] is not None:
        role.auto_promote = bool(updates["auto_promote"])
    if "suppressed_org_criterion_ids" in updates:
        raw = updates["suppressed_org_criterion_ids"] or []
        role.suppressed_org_criterion_ids = [int(x) for x in raw]
    try:
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")
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
    # Activation OR resume should kick a daily-review-style cycle so the
    # agent immediately picks up where things stand instead of waiting up
    # to 30 minutes for the cohort-tick beat (or 24h for daily-review).
    # On activation: drain unscored/un-pre-screened backlog. On resume:
    # retry whatever was paused (typically a per-cycle budget exhaustion).
    # Failures here are logged but do not fail the PATCH — the beat
    # sweeps will still catch up eventually.
    if agent_activated_now or agent_resumed_now:
        try:
            from ...tasks.agent_tasks import agent_daily_review_role
            agent_daily_review_role.delay(int(role.id))
        except Exception:
            logger.exception(
                "Failed to enqueue %s cycle for role_id=%s",
                "activation" if agent_activated_now else "resume",
                role.id,
            )
        # Toggling the agent on from Settings doesn't run a chat turn, so if the
        # role still carries OLD-engine scores, drop an opt-in re-score offer
        # into its agent chat — the recruiter steers the scope when they open it.
        if agent_activated_now:
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
    role.interview_focus = None
    role.interview_focus_generated_at = None

    try:
        sync_derived_criteria(db, role)
        mark_role_scores_stale(db, role.id)
        db.commit()
        db.refresh(role)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload job spec")

    # Auto-trigger interview-focus generation in the background. The
    # request returns immediately; the worker writes interview_focus +
    # pack templates back onto the role row when Claude responds.
    on_role_jd_attached(role)

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
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "difficulty": t.difficulty,
            "duration_minutes": t.duration_minutes,
            "task_type": t.task_type,
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
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unlink task from role")
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
