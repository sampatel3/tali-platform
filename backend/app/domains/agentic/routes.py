"""HTTP routes for the autonomous recruiting agent.

  GET    /api/v1/agent-decisions                  list pending (or any-status) decisions
  POST   /api/v1/agent-decisions/{id}/approve     execute the agent's recommendation
  POST   /api/v1/agent-decisions/{id}/override    discard recommendation; recruiter acts manually
  POST   /api/v1/agent-decisions/discard          bulk discard pending decisions for a role (opt-in "also discard" on turn-off)
  GET    /api/v1/agent-runs                       recent autonomous-cycle log
  POST   /api/v1/roles/{id}/agent/run-now         enqueue a manual agent cycle
  POST   /api/v1/roles/{id}/agent/pause           soft-pause one role (keeps pending decisions)
  POST   /api/v1/roles/{id}/agent/resume          resume one paused role (if back under cap)
  POST   /api/v1/agent/pause-all                  pause every currently-running role
  POST   /api/v1/agent/resume-all                 resume every paused enabled role

All endpoints are org-scoped via ``get_current_user``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, or_
from sqlalchemy.orm import Session

from ...actions import approve_decision as approve_decision_action
from ...actions import override_decision as override_decision_action
from ...actions.decision_execution_authority import require_expected_decision_type
from ...actions.types import Actor
from ...agent_chat.run_history import public_failure_summary
from ...deps import get_current_user
from ...domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...domains.assessments_runtime.role_support import is_resolved, role_family_response, roles_with_families
from ...services.decision_presentation_service import (
    build_decision_explanation,
    candidate_summary_for,
)
from ...services.decision_reevaluation_service import (
    RelatedEvaluationUnavailableError,
    count_outdated_pending_decisions,
    re_evaluate_related_decision,
)
from ...services.decision_role_context import (
    is_cross_role_decision,
    load_related_assessment_map,
    load_related_evaluation,
    load_related_evaluation_map,
    related_decision_staleness,
    resolve_decision_presentation,
)
from ...services.cv_score_orchestrator import supersede_pending_decisions_for_app
from ...services.role_concurrency import (
    assert_role_version,
)
from ...services.role_change_audit import (
    latest_role_change_actor,
)
from ...services.role_family_reject_authority import (
    authorize_bulk_decision_actions,
    authorize_single_decision_action,
)
from ...services.workable_op_runner import AtsJobRunPersistenceError
from ...services.workspace_agent_control import (
    workspace_agent_pause_state,
)
from ...models.agent_decision import AGENT_DECISION_STATUSES, AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import RoleFamilyResponse
from ._activity_feed import confidence_to_float
from ._reasoning_text import humanize_reasoning
from .decision_payload_support import (
    confidence_band as _confidence_band,
    workable_stage_job_id,
)
from .decision_query_pagination import apply_before_cursor
from .decision_side_effect_dispatch import (
    enqueue_decision_side_effects as _enqueue_decision_side_effects,  # noqa: F401
)
from .decision_command_schemas import (
    ApproveBody,
    BULK_OVERRIDE_ACTIONS as _BULK_OVERRIDE_ACTIONS,
    BulkApproveBody,
    BulkApproveFailure,
    BulkApproveResult,
    BulkOverrideBody,
    OverrideBody,
)
from .manual_run_routes import (
    RunNowBody as RunNowBody,
    RunNowResult as RunNowResult,
    _run_now_dispatch_identity as _run_now_dispatch_identity,
    router as manual_run_router,
    run_now as run_now,
)
from .role_control_routes import (
    MANUAL_PAUSE_REASON as MANUAL_PAUSE_REASON,
    RoleAgentPauseResult as RoleAgentPauseResult,
    RoleVersionCommand as RoleVersionCommand,
    _compensate_failed_agent_dispatch as _compensate_failed_agent_dispatch,
    pause_role_agent as pause_role_agent,
    resume_role_agent as resume_role_agent,
    router as role_control_router,
)
from .related_role_recovery_routes import router as related_role_recovery_router
from .status_routes import (
    AgentStatusActivity as AgentStatusActivity,
    AgentStatusCurrentRun as AgentStatusCurrentRun,
    AgentStatusPausedBy as AgentStatusPausedBy,
    AgentStatusPayload as AgentStatusPayload,
    AgentStatusPendingBreakdown as AgentStatusPendingBreakdown,
    agent_activity as agent_activity,
    agent_status as agent_status,
    router as status_router,
)
from .workspace_control_routes import (
    BulkAgentPauseResult as BulkAgentPauseResult,
    WorkspaceControlCommand as WorkspaceControlCommand,
    _workspace_control_conflict as _workspace_control_conflict,
    pause_all_agents as pause_all_agents,
    resume_all_agents as resume_all_agents,
    router as workspace_control_router,
)


router = APIRouter(tags=["agentic"])

logger = logging.getLogger("taali.agentic.routes")


# Filter shorthands map a single ``?type=`` value onto the set of underlying
# decision_types it should match, so the Hub's filter tabs line up 1:1 with
# the header pending buckets. ``advance`` scopes to interview hand-offs;
# ``assessment`` scopes to the send/resend-assessment pair (one concept to a
# recruiter, two decision_types under the hood). ``all_rejects`` powers the
# combined analytics lens; the Hub can still send either concrete reject type
# when it needs the pre-screen/post-screen distinction.
DECISION_TYPE_CATEGORIES: dict[str, list[str]] = {
    "advance": ["advance_to_interview"],
    "assessment": ["send_assessment", "resend_assessment_invite"],
    "all_rejects": ["reject", "skip_assessment_reject"],
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentDecisionPayload(BaseModel):
    id: int
    role_id: int
    application_id: int
    agent_run_id: Optional[int]
    decision_type: str
    recommendation: str
    status: str
    reasoning: str
    # The causal reason for the action and the compact candidate synthesis are
    # deliberately separate.  ``reasoning`` is retained for API compatibility;
    # new recruiter surfaces should render these two fields instead.
    decision_explanation: dict[str, Any]
    candidate_summary: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    confidence: Optional[float] = None
    model_version: str
    prompt_version: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by_user_id: Optional[int] = None
    resolution_note: Optional[str] = None
    override_action: Optional[str] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    role_name: Optional[str] = None
    role_family: Optional[RoleFamilyResponse] = None
    # Workable date, legacy candidate copy, then local candidate-freshness date.
    applied_at: Optional[datetime] = None
    # The candidate's headline Tali score, 0–100. Resolved by preferring the
    # score the agent stamped on this decision's evidence (frozen at decision
    # time, present even when the application's score cache is still "pending"),
    # then the application's cached score; within each, Tali composite then
    # role-fit (== Tali pre-assessment). The Hub renders it as a score ring on
    # the card. Null for pre-screen rejects — surfaced before any scoring runs,
    # so there's no score to show.
    taali_score: Optional[float] = None
    # Minimal score-summary carrying the provenance ({score_provenance:
    # {engine_version, scored_at, model}}) so the decision feed / cards can
    # render the "scored {date} · v{version}" line under the score — the same
    # shape the candidate surfaces read.
    score_summary: Optional[dict] = None
    # A capped list of the candidate's top requirement grades ({label, score
    # 0-100, status}) from cv_match_details.requirements_assessment, so the Hub
    # card renders the same requirement bars as the candidate report without a
    # second fetch. None for pre-screen rejects (no scoring yet).
    requirements: Optional[list[dict[str, Any]]] = None
    # Workable shortcode (= role.workable_job_id) so the home-page modal
    # can fetch this role's Workable stages for the Advance / Skip & advance
    # stage <select> without a second round-trip.
    workable_job_id: Optional[str] = None
    # The candidate's LIVE Workable stage + whether it is post-handover
    # (phone/technical/final interview, offer, hired). Approve surfaces use it
    # to warn before a reject is approved for a candidate a human recruiter
    # already advanced in Workable — advice, never a block. Read from the
    # application at serialization time (not frozen on the decision) so a
    # stage move after the card was queued still warns correctly.
    candidate_workable_stage: Optional[str] = None
    candidate_post_handover: bool = False
    # A2 + C5: trust signals computed on every read so recruiters see
    # input freshness, confidence, age and cost without opening the
    # expand-out. ``is_stale`` is the boolean gate the Hub uses to
    # disable Approve and prompt Re-evaluate; ``staleness_reasons``
    # carries the machine codes (criteria_changed, cv_replaced, etc.);
    # ``staleness_summary`` is the one-line human label.
    is_stale: bool = False
    staleness_reasons: list[str] = []
    staleness_summary: Optional[str] = None
    # C5: derived, presentational. ``age_seconds`` = now - created_at;
    # ``confidence_band`` maps confidence into high/medium/low for the
    # purple-tone chip; ``cost_usd_cents`` rolls up token_spend so the
    # always-visible footer can show "$0.04" without a JSON dig.
    age_seconds: int = 0
    confidence_band: Optional[str] = None
    cost_usd_cents: int = 0
    # A score job (CvScoreJob pending/running) is currently re-computing this
    # candidate's score — e.g. the recruiter pressed Re-evaluate on an
    # old-engine score, or an agent-chat bulk re-score touched them. The queue
    # greys the row + card and disables actions until the fresh score lands,
    # so nothing is approved on a score that's being replaced mid-flight.
    rescore_in_flight: bool = False


class AgentRunPayload(BaseModel):
    id: int
    role_id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    input_tokens: int
    output_tokens: int
    total_cost_micro_usd: int
    decisions_emitted: int
    tools_called: Optional[list[dict[str, Any]]]
    error: Optional[str]
    model_version: Optional[str]
    prompt_version: Optional[str]


class DiscardBody(BaseModel):
    role_id: int
    expected_version: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision_to_payload(
    decision: AgentDecision,
    candidate: Optional[Candidate],
    role: Optional[Role] = None,
    *,
    application: Optional[CandidateApplication] = None,
    related_evaluation: Optional[SisterRoleEvaluation] = None,
    is_stale: bool = False,
    staleness_reasons: Optional[list[str]] = None,
    staleness_summary: Optional[str] = None,
    rescore_in_flight: bool = False,
) -> AgentDecisionPayload:
    # C5: derive presentational fields here so the API answers "is this
    # safe to approve, how old is it, how expensive was it" without
    # forcing the UI to compute it from JSON.
    confidence_value = confidence_to_float(decision.confidence)
    age_seconds = 0
    if decision.created_at is not None:
        created = decision.created_at
        # Defensive: prod Postgres returns tz-aware datetimes, but guard
        # against a naive value so a single odd row can't 500 the whole
        # decisions list (this is the recruiter's primary queue).
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - created
        age_seconds = max(0, int(delta.total_seconds()))
    cost_micro = 0
    token_spend = decision.token_spend or {}
    if isinstance(token_spend, dict):
        cost_micro = int(token_spend.get("total_micro_usd") or 0)
    cost_cents = cost_micro // 10_000

    presentation = resolve_decision_presentation(
        decision,
        application=application,
        related_evaluation=related_evaluation,
        rescore_in_flight=rescore_in_flight,
    )

    return AgentDecisionPayload(
        id=int(decision.id),
        role_id=int(decision.role_id),
        application_id=int(decision.application_id),
        agent_run_id=int(decision.agent_run_id) if decision.agent_run_id else None,
        decision_type=str(decision.decision_type),
        recommendation=str(decision.recommendation),
        status=str(decision.status),
        reasoning=humanize_reasoning(str(decision.reasoning)),
        decision_explanation=build_decision_explanation(
            decision, presentation.scoring_application
        ),
        candidate_summary=candidate_summary_for(
            decision,
            presentation.scoring_application,
            role_summary=presentation.role_summary,
        ),
        evidence=decision.evidence,
        confidence=confidence_value,
        model_version=str(decision.model_version),
        prompt_version=str(decision.prompt_version),
        created_at=decision.created_at,
        resolved_at=decision.resolved_at,
        resolved_by_user_id=decision.resolved_by_user_id,
        resolution_note=decision.resolution_note,
        override_action=decision.override_action,
        candidate_name=getattr(candidate, "full_name", None) if candidate else None,
        candidate_email=getattr(candidate, "email", None) if candidate else None,
        role_name=getattr(role, "name", None) if role else None,
        role_family=role_family_response(role) if role else None,
        applied_at=(
            (getattr(application, "workable_created_at", None) if application else None)
            # Candidate-level copy only for Workable rows — a manual application
            # on a person who ALSO applied via Workable must not inherit the
            # other application's date.
            or (
                getattr(candidate, "workable_created_at", None)
                if candidate is not None
                and application is not None
                and getattr(application, "source", None) == "workable"
                else None
            )
            or (getattr(application, "created_at", None) if application else None)
        ),
        taali_score=presentation.taali_score,
        score_summary=(
            {
                "score_provenance": presentation.score_provenance,
                "integrity": presentation.integrity,
            }
            if (presentation.score_provenance or presentation.integrity)
            else None
        ),
        requirements=presentation.requirements,
        workable_job_id=workable_stage_job_id(role, application),
        candidate_workable_stage=(
            getattr(application, "workable_stage", None) if application else None
        ),
        candidate_post_handover=bool(
            application is not None
            and is_post_handover_workable_stage(
                getattr(application, "workable_stage", None)
            )
        ),
        is_stale=is_stale,
        staleness_reasons=staleness_reasons or [],
        staleness_summary=staleness_summary,
        rescore_in_flight=presentation.rescore_in_flight,
        age_seconds=age_seconds,
        confidence_band=_confidence_band(confidence_value),
        cost_usd_cents=cost_cents,
    )


def _run_to_payload(run: AgentRun) -> AgentRunPayload:
    return AgentRunPayload(
        id=int(run.id),
        role_id=int(run.role_id),
        trigger=str(run.trigger),
        status=str(run.status),
        started_at=run.started_at,
        finished_at=run.finished_at,
        input_tokens=int(run.input_tokens or 0),
        output_tokens=int(run.output_tokens or 0),
        total_cost_micro_usd=int(run.total_cost_micro_usd or 0),
        decisions_emitted=int(run.decisions_emitted or 0),
        tools_called=run.tools_called,
        error=public_failure_summary(run.error),
        model_version=run.model_version,
        prompt_version=run.prompt_version,
    )


# ---------------------------------------------------------------------------
# GET /agent-decisions
# ---------------------------------------------------------------------------


@router.get("/agent-decisions", response_model=list[AgentDecisionPayload])
def list_agent_decisions(
    role_id: Optional[int] = Query(default=None),
    application_id: Optional[int] = Query(default=None),
    status: str = Query(default="pending"),
    decision_type: Optional[str] = Query(default=None, alias="type"),
    q: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    before_created_at: Optional[datetime] = Query(default=None),
    before_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if status not in AGENT_DECISION_STATUSES and status not in (
        "all",
        "resolved",
        "decided",
        "current",
    ):
        raise HTTPException(status_code=422, detail=f"unsupported status={status!r}")

    query = (
        db.query(AgentDecision, Candidate, Role)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .outerjoin(Role, Role.id == AgentDecision.role_id)
        .filter(AgentDecision.organization_id == current_user.organization_id)
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    # Single-candidate lens: the candidate report fetches THIS application's
    # decision(s) to surface the agent's recommendation in its header strip.
    if application_id is not None:
        query = query.filter(AgentDecision.application_id == int(application_id))
    if status != "all":
        if status == "pending":
            # The Hub queue shows actionable (pending) rows AND in-flight
            # (processing) ones — the latter rendered greyed/at-bottom so a
            # recruiter can't double-approve while the background batch runs.
            query = query.filter(AgentDecision.status.in_(("pending", "processing")))
        elif status == "resolved":
            # History: the inverse of the queue — every decision that has
            # left the recruiter's queue (approved / overridden / taught /
            # discarded / expired). Excludes the live queue states
            # (pending, processing) which are still actionable elsewhere.
            query = query.filter(AgentDecision.status.notin_(("pending", "processing")))
        elif status == "decided":
            # Calls a human actually made — approved or overridden — and
            # nothing else. Narrower than ``resolved`` on purpose: the Hub's
            # "Recent decisions" panel shows these under a row limit, and
            # folding in bulk discarded/expired rows (a purged queue can
            # produce hundreds at once) would push genuine decisions out of
            # the window and blank the panel.
            query = query.filter(AgentDecision.status.in_(("approved", "overridden")))
        elif status == "current":
            # Candidate-report lens: an actionable recommendation wins over
            # history; otherwise retain the last decision a human actually
            # made. Purge artefacts (discarded/expired) must never displace it.
            query = query.filter(
                AgentDecision.status.in_(
                    (
                        "pending",
                        "processing",
                        "reverted_for_feedback",
                        "approved",
                        "overridden",
                    )
                )
            )
        else:
            query = query.filter(AgentDecision.status == status)
    # Snooze: when listing pending, hide rows whose snooze hasn't elapsed.
    if status == "pending":
        now = datetime.now(timezone.utc)
        query = query.filter(
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            )
        )
    if decision_type:
        types = DECISION_TYPE_CATEGORIES.get(decision_type, [decision_type])
        query = query.filter(AgentDecision.decision_type.in_(types))
    if since is not None:
        query = query.filter(AgentDecision.created_at >= since)
    if q:
        # Cheap text search across candidate name/email + reasoning. Good
        # enough for a typeahead; if scale demands it, we move to a
        # dedicated FTS column later.
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
                AgentDecision.reasoning.ilike(like),
            )
        )
    query = apply_before_cursor(
        query,
        before_created_at=before_created_at,
        before_id=before_id,
        status=status,
    )
    # ``created_at`` is the transaction timestamp (server_default=func.now()), so
    # every row written in one bulk-scoring transaction shares an identical value.
    # Order by the unique primary key as a tiebreaker to give a total, stable order —
    # otherwise tied rows shuffle on every reload and the LIMIT cutoff flickers.
    if status == "current":
        live_first = case(
            (
                AgentDecision.status.in_(
                    ("pending", "processing", "reverted_for_feedback")
                ),
                0,
            ),
            else_=1,
        )
        query = query.order_by(
            live_first, desc(AgentDecision.created_at), desc(AgentDecision.id)
        )
    else:
        query = query.order_by(desc(AgentDecision.created_at), desc(AgentDecision.id))
    query = query.limit(limit)
    rows = query.all()
    family_roles = roles_with_families(db, [role.id for _, _, role in rows if role], organization_id=int(current_user.organization_id))

    # A2: compute staleness per row. Only meaningful for ``pending``
    # rows (resolved decisions are frozen snapshots per A6); for
    # resolved/discarded/expired we skip the computation and return
    # is_stale=False. Done in-loop because each row needs the
    # application + role context; the staleness service handles
    # caching internally and reuses already-loaded entities when
    # callers pass them in.
    from ...services import decision_staleness
    # CandidateApplication must stay module-scoped; a local import would shadow
    # the earlier join and raise UnboundLocalError.

    # Batch-load every returned application for staleness and card scores.
    all_app_ids = [int(decision.application_id) for decision, _, _ in rows]
    apps_by_id: dict[int, CandidateApplication] = {}
    if all_app_ids:
        for app in (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(all_app_ids))
            .all()
        ):
            apps_by_id[int(app.id)] = app
    related_evaluations = load_related_evaluation_map(
        db,
        decisions=[decision for decision, _, _ in rows],
        applications_by_id=apps_by_id,
    )
    related_assessments = load_related_assessment_map(
        db,
        decisions=[decision for decision, _, _ in rows],
        applications_by_id=apps_by_id,
    )

    # One query: which of these candidates have a score job in flight right
    # now (Re-evaluate on an old-engine score, agent-chat bulk re-score, …)?
    # The queue greys those rows and disables their actions until the fresh
    # score lands, so a recruiter never approves a score being replaced.
    rescoring_app_ids: set[int] = set()
    if all_app_ids:
        from ...models.cv_score_job import (
            SCORE_JOB_PENDING,
            SCORE_JOB_RUNNING,
            CvScoreJob,
        )

        rescoring_app_ids = {
            int(row[0])
            for row in db.query(CvScoreJob.application_id)
            .filter(
                CvScoreJob.application_id.in_(all_app_ids),
                CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
            )
            .distinct()
            .all()
        }

    # One cache for the whole page: pending rows in a queue typically share
    # a handful of roles, so this collapses the per-role criteria/note
    # lookups from O(rows) to O(distinct roles).
    staleness_cache = decision_staleness.StalenessCache()

    payloads: list[AgentDecisionPayload] = []
    for decision, candidate, role in rows:
        role = family_roles.get(int(role.id), role) if role else None
        app = apps_by_id.get(int(decision.application_id))
        related_evaluation = related_evaluations.get(
            (int(decision.role_id), int(decision.application_id))
        )
        related_assessment = related_assessments.get(int(decision.id))
        cross_role = is_cross_role_decision(decision, app)
        is_stale = False
        reasons: list[str] = []
        summary: Optional[str] = None
        if decision.status == "pending":
            try:
                report = (
                    related_decision_staleness(
                        db,
                        decision,
                        related_evaluation,
                        application=app,
                        role=role,
                        cache=staleness_cache,
                        assessment=related_assessment,
                    )
                    if cross_role
                    else decision_staleness.evaluate(
                        db,
                        decision,
                        application=app,
                        role=role,
                        cache=staleness_cache,
                    )
                )
                is_stale = report.is_stale
                reasons = report.reasons
                summary = report.summary
            except Exception:  # pragma: no cover — defensive
                pass
        payloads.append(
            _decision_to_payload(
                decision,
                candidate,
                role,
                application=app,
                related_evaluation=related_evaluation,
                is_stale=is_stale,
                staleness_reasons=reasons,
                staleness_summary=summary,
                rescore_in_flight=int(decision.application_id) in rescoring_app_ids,
            )
        )
    return payloads


# ---------------------------------------------------------------------------
# GET /agent-decisions/needs-reeval-count
# ---------------------------------------------------------------------------


class NeedsReevalCount(BaseModel):
    count: int


@router.get("/agent-decisions/needs-reeval-count", response_model=NeedsReevalCount)
def needs_reeval_count(
    role_id: Optional[int] = Query(default=None),
    decision_type: Optional[str] = Query(default=None, alias="type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Count of PENDING decisions scored by an OLD engine (the "needs re-eval"
    backlog) for the current role/type scope.

    The home "Needs re-eval" pill reads this so its number reflects the WHOLE
    queue, not the page-fetch window — the per-row is_stale on the list is only
    computed for the (capped) rows returned, which silently under-counts a deep
    backlog (e.g. ~63 shown vs ~2,300 real).

    Counts ENGINE staleness only (the dominant "old model" case the pill is
    about), not the rarer input-change staleness the per-card banner also flags
    — so on a multi-thousand-row queue this is one cheap pass instead of a
    per-row evaluate that loads + hashes every CV (~9x faster). It pulls only
    the two engine-version sub-keys from cv_match_details and reuses the real
    score_is_outdated / is_resolved via a lightweight shim, so there's no
    staleness logic duplicated in SQL.
    """
    types = (
        DECISION_TYPE_CATEGORIES.get(decision_type, [decision_type])
        if decision_type
        else None
    )
    return NeedsReevalCount(
        count=count_outdated_pending_decisions(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
            decision_types=types,
        )
    )


# ---------------------------------------------------------------------------
# POST /agent-decisions/{id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/agent-decisions/{decision_id}/approve", response_model=AgentDecisionPayload
)
def approve(
    decision_id: int,
    body: ApproveBody = Body(default_factory=ApproveBody),
    force: bool = Query(
        default=False, description="Approve even if the inputs are stale (A4)"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # A4: refuse to approve a stale decision unless the recruiter
    # explicitly forces it. The Hub disables Approve when is_stale=True
    # and surfaces a Re-evaluate button; this 409 is the second line of
    # defense for direct API consumers and copy-pasted URLs. Compute
    # staleness on the row first; resolved/discarded decisions are
    # handled by the existing status check inside approve_decision.run.
    pre_decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == current_user.organization_id,
        )
        .first()
    )
    if pre_decision is not None:
        authorize_single_decision_action(
            db,
            current_user=current_user,
            role_id=int(pre_decision.role_id),
            reject=(
                pre_decision.status == "pending"
                and str(pre_decision.decision_type)
                in ("reject", "skip_assessment_reject")
            ),
            expected=body.expected_role_family,
        )
        require_expected_decision_type(
            decision_id=int(pre_decision.id),
            expected=body.expected_decision_type,
            current=str(pre_decision.decision_type),
            required=pre_decision.status == "pending",
        )
    if pre_decision is not None and pre_decision.status == "pending" and not force:
        from ...services import decision_staleness

        try:
            pre_application = db.get(
                CandidateApplication, int(pre_decision.application_id)
            )
            pre_related_evaluation = load_related_evaluation(
                db,
                decision=pre_decision,
                application=pre_application,
            )
            report = (
                related_decision_staleness(
                    db,
                    pre_decision,
                    pre_related_evaluation,
                    application=pre_application,
                )
                if is_cross_role_decision(pre_decision, pre_application)
                else decision_staleness.evaluate(
                    db, pre_decision, application=pre_application
                )
            )
            if report.is_stale:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "decision_stale",
                        "message": (
                            "Inputs cited by this decision have changed since "
                            "it was queued. Re-evaluate or approve with force=true."
                        ),
                        "reasons": report.reasons,
                        "summary": report.summary,
                    },
                )
        except HTTPException:
            raise
        except Exception:  # pragma: no cover — defensive
            pass

    try:
        # Optimistic + async: flip to 'processing' (stays in the queue, greyed)
        # and hand the Workable writeback to the background batch task,
        # serialized per org. Returns immediately; the task commits the local
        # change only after Workable confirms, and on failure returns the
        # decision to the queue. (In tests Celery runs eagerly, so the task has
        # already finished and the refresh below shows the final status.)
        decision = approve_decision_action.enqueue_one(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_id=decision_id,
            note=body.note,
            workable_target_stage=body.workable_target_stage,
            expected_decision_type=body.expected_decision_type,
            expected_role_family=body.expected_role_family,
        )
        db.refresh(decision)
    except HTTPException:
        db.rollback()
        raise
    except AtsJobRunPersistenceError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "ATS operation was not queued because durable tracking is "
                "temporarily unavailable. No provider update was sent; try again."
            ),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to approve agent decision %s", decision_id)
        raise HTTPException(
            status_code=500, detail="Failed to approve decision"
        ) from exc
    candidate = (
        db.query(Candidate)
        .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    role = db.query(Role).filter(Role.id == decision.role_id).first()
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    related_evaluation = load_related_evaluation(
        db, decision=decision, application=application
    )
    return _decision_to_payload(
        decision,
        candidate,
        role,
        application=application,
        related_evaluation=related_evaluation,
    )


# ---------------------------------------------------------------------------
# POST /agent-decisions/{id}/override
# ---------------------------------------------------------------------------


@router.post(
    "/agent-decisions/{decision_id}/override", response_model=AgentDecisionPayload
)
def override(
    decision_id: int,
    body: OverrideBody = Body(default_factory=OverrideBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == current_user.organization_id,
        )
        .first()
    )
    if target is not None:
        authorize_single_decision_action(
            db,
            current_user=current_user,
            role_id=int(target.role_id),
            reject=(
                str(body.override_action or "") == "reject"
                and target.status in ("pending", "reverted_for_feedback")
            ),
            expected=body.expected_role_family,
        )
        require_expected_decision_type(
            decision_id=int(target.id),
            expected=body.expected_decision_type,
            current=str(target.decision_type),
            required=target.status in ("pending", "reverted_for_feedback"),
        )
    try:
        if (body.override_action or "") == "skip_assessment_advance":
            # "Skip & advance" no longer advances + writes Workable directly
            # (it couldn't reliably collect the target stage — an empty stage
            # list silently advanced Tali-internal only). It now reclassifies
            # the card into the advance queue — synchronous, no Workable op —
            # where the normal advance flow collects the stage on approval.
            decision = override_decision_action.reclassify_to_advance_queue(
                db,
                Actor.recruiter(current_user),
                organization_id=current_user.organization_id,
                decision_id=decision_id,
                note=body.note,
                expected_decision_type=body.expected_decision_type,
            )
        else:
            # Optimistic + async: flip to 'processing' and run the override via
            # the serialized Workable runner. State-change actions (reject/
            # advance) are gated on Workable and re-queue on failure — no more
            # silent 429 drops. (Eager Celery in tests finishes inline.)
            decision = override_decision_action.enqueue(
                db,
                Actor.recruiter(current_user),
                organization_id=current_user.organization_id,
                decision_id=decision_id,
                override_action=body.override_action,
                note=body.note,
                workable_target_stage=body.workable_target_stage,
                expected_decision_type=body.expected_decision_type,
                expected_role_family=body.expected_role_family,
            )
        db.refresh(decision)
    except HTTPException:
        db.rollback()
        raise
    except AtsJobRunPersistenceError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "ATS operation was not queued because durable tracking is "
                "temporarily unavailable. No provider update was sent; try again."
            ),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to override agent decision %s", decision_id)
        raise HTTPException(
            status_code=500, detail="Failed to override decision"
        ) from exc
    candidate = (
        db.query(Candidate)
        .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    role = db.query(Role).filter(Role.id == decision.role_id).first()
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    related_evaluation = load_related_evaluation(
        db, decision=decision, application=application
    )
    return _decision_to_payload(
        decision,
        candidate,
        role,
        application=application,
        related_evaluation=related_evaluation,
    )


# ---------------------------------------------------------------------------
# POST /agent-decisions/{id}/re-evaluate
# ---------------------------------------------------------------------------


class ReEvaluateResult(BaseModel):
    decision_id: int
    role_id: int
    application_id: int
    superseded: int
    queued: bool
    task_id: Optional[str] = None
    detail: Optional[str] = None
    blocked: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None


@router.post(
    "/agent-decisions/{decision_id}/re-evaluate", response_model=ReEvaluateResult
)
def re_evaluate(
    decision_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A4: discard a stale pending decision and ask the agent to decide
    again on fresh inputs.

    Surfaced by the Decision Hub when a decision's inputs have changed
    (is_stale=True): the recruiter presses "Re-evaluate" instead of
    approving a decision built on outdated criteria / CV / scores. We
    discard the pending row (preserving its audit trail) and enqueue a
    focused agent cycle for the candidate.

    A6 invariant: resolved applications (rejected / hired / advanced) are
    frozen — their decision snapshot is the permanent record and must not
    be re-evaluated. Returns 409 in that case.
    """
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == current_user.organization_id,
        )
        .first()
    )
    if decision is None:
        raise HTTPException(
            status_code=404, detail=f"agent_decision {decision_id} not found"
        )
    require_job_permission(
        db,
        current_user=current_user,
        role_id=int(decision.role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    if decision.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"decision {decision_id} is {decision.status}, not pending — nothing to re-evaluate",
        )

    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    if application is not None and is_resolved(application):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "application_resolved",
                "message": (
                    "This candidate has left Tali's flow (rejected/hired/advanced). "
                    "The decision is a frozen audit record and can't be re-evaluated."
                ),
            },
        )

    role = (
        db.query(Role)
        .filter(
            Role.id == decision.role_id,
            Role.organization_id == current_user.organization_id,
        )
        .first()
    )
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )

    try:
        related_result = re_evaluate_related_decision(
            db,
            decision=decision,
            application=application,
            role=role,
            workspace_paused=bool(workspace_pause["paused"]),
        )
    except RelatedEvaluationUnavailableError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "related_role_evaluation_missing",
                "message": "This role's evaluation is unavailable and cannot be refreshed.",
            },
        )
    if related_result is not None:
        return ReEvaluateResult(**related_result)

    # Old-MODEL staleness re-SCORES rather than re-decides: a discard + agent
    # re-run would only re-run the SAME stale score. Enqueue a forced re-score
    # on the current engine; its completion reconciles this candidate's pending
    # decision (a verdict flip auto-corrects, a gated/advanced one stays in the
    # queue), so we don't supersede here. Mirrors the agent-chat bulk re-score
    # (agent_chat.rescore) and works even when the role agent is paused — the
    # score still refreshes to the current engine.
    from ...services.cv_score_orchestrator import enqueue_score, score_is_outdated

    if application is not None and score_is_outdated(application):
        try:
            job = enqueue_score(
                db,
                application,
                force=True,
                bypass_pre_screen=True,
                requires_active_agent=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("re-score failed decision_id=%s", decision_id)
            raise HTTPException(
                status_code=500, detail="Re-score failed. Please try again."
            )
        return ReEvaluateResult(
            decision_id=decision_id,
            role_id=int(decision.role_id),
            application_id=int(decision.application_id),
            superseded=0,
            queued=job is not None,
            task_id=None,
            detail=(
                "re-scoring on the current engine; the decision refreshes when scoring completes"
                if job is not None
                else "could not enqueue a re-score (no CV / spec / API key)"
            ),
        )

    from .reevaluation_dispatch import kick, persist_intent

    try:
        superseded, runnable = persist_intent(
            db,
            decision=decision,
            role=role,
            supersede=supersede_pending_decisions_for_app,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to re-evaluate agent decision %s", decision_id)
        raise HTTPException(
            status_code=500, detail="Failed to re-evaluate decision"
        ) from exc
    # Enqueue a focused cycle. If the role is paused we still discarded the
    # stale decision (the recruiter asked for it) but report queued=False.
    queued = False
    task_id: Optional[str] = None
    detail: Optional[str] = None
    if runnable and not bool(workspace_pause["paused"]):
        queued, task_id = kick(int(decision.id))
        if not queued:
            detail = "stale decision discarded; re-evaluation saved for automatic retry"
    elif bool(workspace_pause["paused"]):
        detail = (
            "stale decision discarded; agent re-run blocked while the workspace "
            "agent is paused; the durable re-evaluation will retry after resume"
        )
    else:
        detail = "stale decision discarded; agent not re-run (role paused or agentic mode off)"

    return ReEvaluateResult(
        decision_id=decision_id,
        role_id=int(decision.role_id),
        application_id=int(decision.application_id),
        superseded=superseded,
        queued=queued,
        task_id=task_id,
        detail=detail,
        blocked=not queued,
        pause_scope=(
            "workspace"
            if bool(workspace_pause["paused"])
            else (
                "role"
                if role is not None and role.agent_paused_at is not None
                else None
            )
        ),
    )


# ---------------------------------------------------------------------------
# POST /agent-decisions/discard
# ---------------------------------------------------------------------------


class DiscardResult(BaseModel):
    role_id: int
    discarded: int


@router.post("/agent-decisions/discard", response_model=DiscardResult)
def discard_pending_for_role(
    body: DiscardBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=body.role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
            "agentic_mode_enabled": bool(role.agentic_mode_enabled),
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=int(role.id),
        ),
    )
    if bool(role.agentic_mode_enabled):
        raise HTTPException(
            status_code=409,
            detail=(
                "Pending decisions can be discarded from Turn off only while "
                "the agent remains disabled at that exact job version."
            ),
        )

    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.role_id == body.role_id,
            AgentDecision.status == "pending",
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for decision in pending:
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolved_by_user_id = current_user.id
        decision.resolution_note = "Discarded — agentic mode toggled off"
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to discard decisions")
    return DiscardResult(role_id=body.role_id, discarded=len(pending))


# ---------------------------------------------------------------------------
# POST /agent-decisions/bulk-approve
# ---------------------------------------------------------------------------


@router.post("/agent-decisions/bulk-approve", response_model=BulkApproveResult)
def bulk_approve(
    body: BulkApproveBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept a list of pending decisions as ONE background approval job.

    Each valid decision is flipped to ``processing`` (so the Hub shows it
    in-flight rather than letting the recruiter double-approve), a single
    ``BackgroundJobRun`` is recorded for Settings, and one batch task drains
    the Workable writebacks sequentially per org so a 100-decision batch can't
    breach the rate limit. Invalid rows (already-resolved, missing) are
    reported in ``failures`` without halting the batch; a decision whose
    Workable writeback ultimately fails is returned to the queue by the task.
    """
    requested = list(
        dict.fromkeys(int(x) for x in body.decision_ids)
    )  # de-dupe, preserve order
    note = (body.note or "").strip() or None
    decision_rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id.in_(requested),
            AgentDecision.organization_id == current_user.organization_id,
        )
        .all()
    )
    authorize_bulk_decision_actions(
        db,
        current_user=current_user,
        decisions=decision_rows,
        reject_action="approve",
        expected_families=body.expected_role_families,
    )
    expected_types = body.expected_decision_types or {}
    for row in decision_rows:
        require_expected_decision_type(
            decision_id=int(row.id),
            expected=expected_types.get(str(row.id)),
            current=str(row.decision_type),
            required=row.status == "pending",
        )
    try:
        result = approve_decision_action.enqueue_batch(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_ids=requested,
            note=note,
            workable_target_stages=body.workable_target_stages or None,
            expected_decision_types=expected_types,
            expected_role_families={
                str(row.id): (body.expected_role_families or {}).get(str(row.role_id))
                for row in decision_rows
                if str(row.decision_type) in ("reject", "skip_assessment_reject")
            },
        )
    except AtsJobRunPersistenceError:
        raise HTTPException(
            status_code=503,
            detail=(
                "ATS operation was not queued because durable tracking is "
                "temporarily unavailable. No provider update was sent; try again."
            ),
        )
    failures = [
        BulkApproveFailure(decision_id=f["decision_id"], error=f["error"])
        for f in result["failures"]
    ]
    return BulkApproveResult(
        requested=len(requested),
        accepted=len(result["accepted"]),
        job_run_id=result["job_run_id"],
        failures=failures,
    )


@router.post("/agent-decisions/bulk-override", response_model=BulkApproveResult)
def bulk_override(
    body: BulkOverrideBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply ONE override action to a list of pending decisions — e.g. bulk
    "Skip & advance" over a screen of send_assessment cards.

    Each valid decision is flipped to ``processing`` and its override is enqueued
    on the serialized per-org Workable runner (so a 100-row bulk can't breach the
    rate limit); a decision whose writeback fails is returned to the queue by the
    runner. Invalid rows (already-resolved, missing) are reported in ``failures``
    without halting the batch. Advance-type actions resolve their Workable stage
    from ``workable_target_stages`` by the decision's ``role_id`` (the same map
    bulk approve uses). An active Workable-linked role without a stage is
    returned to the queue instead of being reported as a local-only success.
    """
    action = (body.override_action or "").strip()
    if action not in _BULK_OVERRIDE_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported bulk override_action={action!r}; expected one of {sorted(_BULK_OVERRIDE_ACTIONS)}",
        )
    requested = list(
        dict.fromkeys(int(x) for x in body.decision_ids)
    )  # de-dupe, preserve order
    note = (body.note or "").strip() or None
    stages = body.workable_target_stages or {}

    rows = {
        d.id: d
        for d in db.query(AgentDecision)
        .filter(
            AgentDecision.id.in_(requested),
            AgentDecision.organization_id == current_user.organization_id,
        )
        .all()
    }
    authorize_bulk_decision_actions(
        db,
        current_user=current_user,
        decisions=rows.values(),
        reject_action="override" if action == "reject" else "none",
        expected_families=body.expected_role_families,
    )
    expected_types = body.expected_decision_types or {}
    for row in rows.values():
        require_expected_decision_type(
            decision_id=int(row.id),
            expected=expected_types.get(str(row.id)),
            current=str(row.decision_type),
            required=row.status in ("pending", "reverted_for_feedback"),
        )
    accepted: list[int] = []
    failures: list[BulkApproveFailure] = []
    for decision_id in requested:
        decision = rows.get(decision_id)
        if decision is None:
            failures.append(
                BulkApproveFailure(decision_id=decision_id, error="not found")
            )
            continue
        stage = (
            stages.get(str(decision.role_id)) if decision.role_id is not None else None
        )
        try:
            if action == "skip_assessment_advance":
                # Reclassify into the advance queue (sync, no Workable write);
                # the stage is collected later when the advance is approved.
                override_decision_action.reclassify_to_advance_queue(
                    db,
                    Actor.recruiter(current_user),
                    organization_id=current_user.organization_id,
                    decision_id=decision_id,
                    note=note,
                    expected_decision_type=expected_types.get(str(decision_id)),
                )
            else:
                override_decision_action.enqueue(
                    db,
                    Actor.recruiter(current_user),
                    organization_id=current_user.organization_id,
                    decision_id=decision_id,
                    override_action=action,
                    note=note,
                    workable_target_stage=stage,
                    expected_decision_type=expected_types.get(str(decision_id)),
                    expected_role_family=(body.expected_role_families or {}).get(
                        str(decision.role_id)
                    ),
                )
            accepted.append(decision_id)
        except AtsJobRunPersistenceError as exc:
            # Earlier rows may already have their own durable jobs and be
            # running. Keep this as an accurate per-decision partial failure
            # instead of aborting with a false global "nothing was sent".
            failures.append(
                BulkApproveFailure(
                    decision_id=decision_id,
                    error=(
                        "Not queued: durable ATS tracking is temporarily "
                        f"unavailable ({exc.op_type}). No provider update was "
                        "sent for this decision."
                    ),
                )
            )
        except HTTPException as exc:
            failures.append(
                BulkApproveFailure(
                    decision_id=decision_id,
                    error=str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
                )
            )
    return BulkApproveResult(
        requested=len(requested),
        accepted=len(accepted),
        job_run_id=None,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# GET /agent-runs
# ---------------------------------------------------------------------------


@router.get("/agent-runs", response_model=list[AgentRunPayload])
def list_agent_runs(
    role_id: Optional[int] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(AgentRun).filter(
        AgentRun.organization_id == current_user.organization_id
    )
    if role_id is not None:
        q = q.filter(AgentRun.role_id == int(role_id))
    q = q.order_by(desc(AgentRun.started_at)).limit(limit)
    return [_run_to_payload(r) for r in q.all()]


# ---------------------------------------------------------------------------
# Composed role-agent control routers
# ---------------------------------------------------------------------------


router.include_router(status_router)
router.include_router(manual_run_router)
router.include_router(workspace_control_router)
router.include_router(role_control_router)
router.include_router(related_role_recovery_router)
