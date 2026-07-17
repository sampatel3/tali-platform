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
from ...actions.types import Actor
from ...agent_runtime import budget_guard
from ...deps import get_current_user, require_org_owner
from ...domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    has_job_permission_for_role,
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
    effective_workable_job_id,
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
    bump_role_version,
    role_query_for_update,
)
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    ROLE_CHANGE_ACTION_AGENT_RESUMED,
    add_role_change_event,
    capture_role_change_snapshot,
    infer_legacy_unique_org_actor,
    latest_role_change_actor,
)
from ...services.workable_op_runner import AtsJobRunPersistenceError
from ...services.workspace_agent_control import (
    WORKSPACE_BULK_PAUSE_REASON,
    advance_workspace_control,
    workspace_agent_pause_state,
)
from ...models.agent_decision import AGENT_DECISION_STATUSES, AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.organization import Organization
from ...models.role import Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...schemas.role import RoleFamilyResponse
from ._activity_feed import (
    AgentActivityPayload,
    build_activity_feed,
    confidence_to_float,
)
from ._reasoning_text import humanize_reasoning


router = APIRouter(tags=["agentic"])

logger = logging.getLogger("taali.agentic.routes")


def _enqueue_decision_side_effects(
    decision_id: int,
    *,
    workable_target_stage: Optional[str],
    reject_notify: bool,
) -> None:
    """Fire-and-forget the deferred Workable / graph side effects for a
    resolved decision (see app.tasks.decision_tasks). Best-effort: an
    enqueue failure must never turn a successful approve / override into an
    error for the recruiter — the state change already committed."""
    try:
        from ...tasks.decision_tasks import apply_decision_side_effects

        apply_decision_side_effects.delay(
            int(decision_id),
            workable_target_stage=workable_target_stage,
            reject_notify=bool(reject_notify),
        )
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "failed to enqueue decision side effects decision_id=%s",
            decision_id,
            exc_info=True,
        )


# Filter shorthands map a single ``?type=`` value onto the set of underlying
# decision_types it should match, so the Hub's filter tabs line up 1:1 with
# the header pending buckets. ``advance`` scopes to interview hand-offs;
# ``assessment`` scopes to the send/resend-assessment pair (one concept to a
# recruiter, two decision_types under the hood). ``reject`` and
# ``skip_assessment_reject`` stay 1:1 with their decision_type — the Hub draws
# a hard visual line between post- and pre-screen rejections, and recruiters
# want to filter on that distinction.
DECISION_TYPE_CATEGORIES: dict[str, list[str]] = {
    "advance": ["advance_to_interview"],
    "assessment": ["send_assessment", "resend_assessment_invite"],
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


class ApproveBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)
    # Recruiter's Workable stage pick for advance verdicts (sent from the
    # home-page modal's <select>). Optional — when absent, only Tali's
    # internal pipeline_stage updates.
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)


class OverrideBody(BaseModel):
    override_action: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)


class DiscardBody(BaseModel):
    role_id: int
    expected_version: int = Field(ge=1)


class RunNowBody(BaseModel):
    application_id: Optional[int] = None


class RoleVersionCommand(BaseModel):
    expected_version: int = Field(ge=1)


class AgentStatusActivity(BaseModel):
    event_type: str
    reason: Optional[str] = None
    actor_type: str
    application_id: Optional[int] = None
    candidate_name: Optional[str] = None
    created_at: datetime


class AgentStatusCurrentRun(BaseModel):
    id: int
    started_at: datetime
    status: str
    decisions_emitted: int
    tools_called: Optional[list[dict[str, Any]]] = None


class AgentStatusPausedBy(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    is_current_user: bool
    changed_at: Optional[datetime] = None
    attribution: Literal["verified", "inferred", "unavailable"]
    source: Literal[
        "role_change_event",
        "legacy_unique_member",
        "legacy_history",
        "workspace_control",
    ]


class AgentStatusPendingBreakdown(BaseModel):
    total: int
    decisions: int
    questions: int


class AgentStatusPayload(BaseModel):
    role_id: int
    enabled: bool
    # Viewer-specific capability from the same hiring-team policy enforced by
    # every role agent mutation. Clients use it only to render controls as
    # read-only; the mutation endpoints remain the authority.
    can_control_agent: bool = False
    # Effective state follows workspace > role precedence. The legacy
    # paused_at/reason/by fields remain the effective display contract so old
    # clients stop immediately on a workspace hold; the explicit role_* fields
    # preserve the local desired state underneath that overlay.
    paused: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by: Optional[AgentStatusPausedBy] = None
    role_paused_at: Optional[datetime] = None
    role_paused_reason: Optional[str] = None
    role_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_paused: bool = False
    workspace_paused_at: Optional[datetime] = None
    workspace_paused_reason: Optional[str] = None
    workspace_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_control_version: int = 1
    last_run_at: Optional[datetime] = None
    bootstrap_status: Optional[str] = None
    bootstrap_error: Optional[str] = None
    bootstrap_started_at: Optional[datetime] = None
    bootstrap_completed_at: Optional[datetime] = None
    pending_decisions: int
    pending_breakdown: AgentStatusPendingBreakdown
    monthly_budget_cents: Optional[int] = None
    monthly_spent_cents: int
    current_run: Optional[AgentStatusCurrentRun] = None
    last_activity: Optional[AgentStatusActivity] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confidence_band(value: Optional[float]) -> Optional[str]:
    """C5: bucket the 0-1 confidence into purple-tone tiers for the chip.

    No red/amber — house style is purple-only. Low confidence still
    shows muted purple so the recruiter can spot it without colour
    semantics implying urgency or danger.
    """
    if value is None:
        return None
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


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
        workable_job_id=effective_workable_job_id(role),
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
        error=run.error,
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
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if status not in AGENT_DECISION_STATUSES and status not in ("all", "resolved", "decided", "current"):
        raise HTTPException(status_code=422, detail=f"unsupported status={status!r}")

    query = (
        db.query(AgentDecision, Candidate, Role)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
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
            query = query.filter(
                AgentDecision.status.in_(("pending", "processing"))
            )
        elif status == "resolved":
            # History: the inverse of the queue — every decision that has
            # left the recruiter's queue (approved / overridden / taught /
            # discarded / expired). Excludes the live queue states
            # (pending, processing) which are still actionable elsewhere.
            query = query.filter(
                AgentDecision.status.notin_(("pending", "processing"))
            )
        elif status == "decided":
            # Calls a human actually made — approved or overridden — and
            # nothing else. Narrower than ``resolved`` on purpose: the Hub's
            # "Recent decisions" panel shows these under a row limit, and
            # folding in bulk discarded/expired rows (a purged queue can
            # produce hundreds at once) would push genuine decisions out of
            # the window and blank the panel.
            query = query.filter(
                AgentDecision.status.in_(("approved", "overridden"))
            )
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
                Candidate.name.ilike(like),
                Candidate.email.ilike(like),
                AgentDecision.reasoning.ilike(like),
            )
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
        query = query.order_by(
            desc(AgentDecision.created_at), desc(AgentDecision.id)
        )
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
                decision, candidate, role,
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


@router.post("/agent-decisions/{decision_id}/approve", response_model=AgentDecisionPayload)
def approve(
    decision_id: int,
    body: ApproveBody = Body(default_factory=ApproveBody),
    force: bool = Query(default=False, description="Approve even if the inputs are stale (A4)"),
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
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(pre_decision.role_id),
            permission=JobPermission.CONTROL_AGENT,
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
        raise HTTPException(status_code=500, detail=f"approve failed: {exc}")

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


@router.post("/agent-decisions/{decision_id}/override", response_model=AgentDecisionPayload)
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
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(target.role_id),
            permission=JobPermission.CONTROL_AGENT,
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
        raise HTTPException(status_code=500, detail=f"override failed: {exc}")

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


@router.post("/agent-decisions/{decision_id}/re-evaluate", response_model=ReEvaluateResult)
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
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
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
        except Exception as exc:
            db.rollback()
            logger.exception("re-score failed decision_id=%s", decision_id)
            raise HTTPException(status_code=500, detail="Re-score failed. Please try again.")
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

    try:
        superseded = supersede_pending_decisions_for_app(
            db, int(decision.application_id), reason="recruiter_requested_re_evaluate",
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"re-evaluate failed: {exc}")

    # Enqueue a focused cycle. If the role is paused we still discarded the
    # stale decision (the recruiter asked for it) but report queued=False.
    queued = False
    task_id: Optional[str] = None
    detail: Optional[str] = None
    if (
        role is not None
        and not bool(workspace_pause["paused"])
        and role.agent_paused_at is None
        and bool(role.agentic_mode_enabled)
    ):
        from ...tasks.agent_tasks import agent_manual_run

        async_result = agent_manual_run.delay(
            role_id=int(decision.role_id),
            application_id=int(decision.application_id),
        )
        queued = True
        task_id = str(async_result.id)
    elif bool(workspace_pause["paused"]):
        detail = (
            "stale decision discarded; agent re-run blocked while the workspace "
            "agent is paused"
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
    now = datetime.utcnow()
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


class BulkApproveBody(BaseModel):
    """Explicit IDs — caller sends only the visible / selected rows.

    Refusing an implicit "match all of type X" contract here is
    deliberate: the Hub's filters can mismatch what the recruiter sees
    by milliseconds, and approving everything we *would have* shown is
    a worse failure mode than the request being a no-op when the user
    scrolls before they click.
    """

    decision_ids: list[int] = Field(min_length=1, max_length=500)
    note: Optional[str] = None
    # Per-role Workable target stage for the advance_to_interview decisions in
    # the batch, keyed by ``role_id`` (as a string — JSON object keys are
    # strings). A bulk approve can span roles, each mapped to its own Workable
    # job with its own stage list, so a single global stage doesn't generalize;
    # the Hub modal renders one stage picker per distinct advancing role and
    # sends the picks here. Roles absent from the map (or non-advance decisions)
    # advance on Tali's internal stage only — no Workable move.
    workable_target_stages: Optional[dict[str, str]] = None


class BulkApproveFailure(BaseModel):
    decision_id: int
    error: str


class BulkApproveResult(BaseModel):
    requested: int
    # Number accepted for background processing (flipped to 'processing'). The
    # whole batch becomes ONE background job (job_run_id) that drains the
    # Workable writebacks sequentially per org; a decision whose writeback
    # fails is returned to the queue. Track progress via Settings → Background
    # jobs (GET /background-jobs/runs).
    accepted: int
    job_run_id: Optional[int] = None
    failures: list[BulkApproveFailure] = Field(default_factory=list)


# Override actions the bulk endpoint supports. ``send_assessment`` is excluded —
# that's what bulk *approve* does (run the recommended action); a bulk override
# is for taking a DIFFERENT action across the screen (the Hub's "Skip & advance"
# on send_assessment cards, or bulk reject / advance).
_BULK_OVERRIDE_ACTIONS = {"skip_assessment_advance", "advance", "reject"}


class BulkOverrideBody(BaseModel):
    """Explicit IDs — caller sends only the visible / selected rows (same
    contract as bulk approve; no implicit "all of type X")."""

    decision_ids: list[int] = Field(min_length=1, max_length=500)
    override_action: str
    note: Optional[str] = None
    # Per-role Workable advance stage keyed by ``role_id`` (string) — same shape
    # and source as bulk approve, used for the advance / skip_assessment_advance
    # actions. Roles absent from the map advance on Tali's internal stage only.
    workable_target_stages: Optional[dict[str, str]] = None


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
    requested = list(dict.fromkeys(int(x) for x in body.decision_ids))  # de-dupe, preserve order
    note = (body.note or "").strip() or None
    role_ids = {
        int(role_id)
        for (role_id,) in db.query(AgentDecision.role_id)
        .filter(
            AgentDecision.id.in_(requested),
            AgentDecision.organization_id == current_user.organization_id,
        )
        .distinct()
        .all()
        if role_id is not None
    }
    for role_id in sorted(role_ids):
        require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
        )
    try:
        result = approve_decision_action.enqueue_batch(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_ids=requested,
            note=note,
            workable_target_stages=body.workable_target_stages or None,
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
    bulk approve uses); roles absent advance on Tali's internal stage only.
    """
    action = (body.override_action or "").strip()
    if action not in _BULK_OVERRIDE_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported bulk override_action={action!r}; expected one of {sorted(_BULK_OVERRIDE_ACTIONS)}",
        )
    requested = list(dict.fromkeys(int(x) for x in body.decision_ids))  # de-dupe, preserve order
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
    for role_id in sorted(
        {int(row.role_id) for row in rows.values() if row.role_id is not None}
    ):
        require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
        )
    accepted: list[int] = []
    failures: list[BulkApproveFailure] = []
    for decision_id in requested:
        decision = rows.get(decision_id)
        if decision is None:
            failures.append(BulkApproveFailure(decision_id=decision_id, error="not found"))
            continue
        stage = stages.get(str(decision.role_id)) if decision.role_id is not None else None
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
    q = db.query(AgentRun).filter(AgentRun.organization_id == current_user.organization_id)
    if role_id is not None:
        q = q.filter(AgentRun.role_id == int(role_id))
    q = q.order_by(desc(AgentRun.started_at)).limit(limit)
    return [_run_to_payload(r) for r in q.all()]


# ---------------------------------------------------------------------------
# POST /roles/{id}/agent/run-now
# ---------------------------------------------------------------------------


class RunNowResult(BaseModel):
    role_id: int
    queued: bool
    task_id: Optional[str] = None
    detail: Optional[str] = None
    blocked: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None


@router.get("/roles/{role_id}/agent/status", response_model=AgentStatusPayload)
def agent_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Consolidated agent state for the role — backs the top bar's poll.

    One call returns: enabled flag, paused state, monthly spend vs cap,
    in-flight cycle (if any), pending decision count, and the latest
    agent/recruiter event for the live tick.
    """
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")

    now = datetime.now(timezone.utc)
    # "pending" rolls up both decisions awaiting recruiter approve/override
    # and open orchestrator questions awaiting an answer. The Review queue
    # UI surfaces both kinds in one place — counts must follow.
    pending_decisions_count = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.role_id == role_id,
            AgentDecision.status == "pending",
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .count()
    )
    open_needs_input_count = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == current_user.organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .count()
    )
    pending = int(pending_decisions_count) + int(open_needs_input_count)

    current_run_row = (
        db.query(AgentRun)
        .filter(
            AgentRun.organization_id == current_user.organization_id,
            AgentRun.role_id == role_id,
            AgentRun.status == "running",
        )
        .order_by(desc(AgentRun.started_at))
        .first()
    )
    current_run = (
        AgentStatusCurrentRun(
            id=int(current_run_row.id),
            started_at=current_run_row.started_at,
            status=str(current_run_row.status),
            decisions_emitted=int(current_run_row.decisions_emitted or 0),
            tools_called=current_run_row.tools_called,
        )
        if current_run_row is not None
        else None
    )

    activity_row = (
        db.query(CandidateApplicationEvent, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplicationEvent.actor_type.in_(("agent", "recruiter")),
        )
        .order_by(desc(CandidateApplicationEvent.created_at))
        .limit(1)
        .first()
    )
    last_activity = None
    if activity_row is not None:
        event, candidate = activity_row
        last_activity = AgentStatusActivity(
            event_type=str(event.event_type),
            reason=event.reason,
            actor_type=str(event.actor_type),
            application_id=int(event.application_id),
            candidate_name=getattr(candidate, "full_name", None) if candidate else None,
            created_at=event.created_at,
        )

    monthly_spent = budget_guard.month_to_date_spend_cents(db, role=role)

    role_paused_by = None
    if role.agent_paused_at is not None and budget_guard.is_manual_pause_reason(
        role.agent_paused_reason
    ):
        pause_actor = latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
        )
        if pause_actor is not None:
            # The matching append-only event is the source of truth. Its user
            # can be unavailable after account deletion, but the event time is
            # still useful and must not be replaced with a different member.
            pause_actor_user_id = pause_actor.get("user_id")
            role_paused_by = AgentStatusPausedBy(
                user_id=(
                    int(pause_actor_user_id)
                    if pause_actor_user_id is not None
                    else None
                ),
                name=pause_actor.get("name"),
                is_current_user=(
                    pause_actor_user_id is not None
                    and int(pause_actor_user_id) == int(current_user.id)
                ),
                changed_at=pause_actor.get("changed_at"),
                attribution=(
                    "verified" if pause_actor_user_id is not None else "unavailable"
                ),
                source="role_change_event",
            )
        else:
            # Migration 169 introduced role_change_events without fabricating
            # history for already-paused roles. A sole surviving account that
            # predates such a pause is useful context, but remains explicitly
            # inferred because deleted historical users cannot be recovered.
            inferred_actor = infer_legacy_unique_org_actor(
                db,
                organization_id=int(current_user.organization_id),
                changed_at=role.agent_paused_at,
            )
            inferred_user_id = (
                inferred_actor.get("user_id") if inferred_actor is not None else None
            )
            role_paused_by = AgentStatusPausedBy(
                user_id=(
                    int(inferred_user_id) if inferred_user_id is not None else None
                ),
                name=(
                    inferred_actor.get("name")
                    if inferred_actor is not None
                    else None
                ),
                is_current_user=(
                    inferred_user_id is not None
                    and int(inferred_user_id) == int(current_user.id)
                ),
                changed_at=role.agent_paused_at,
                attribution=("inferred" if inferred_actor is not None else "unavailable"),
                source=(
                    "legacy_unique_member"
                    if inferred_actor is not None
                    else "legacy_history"
                ),
            )

    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    workspace_paused_by = (
        AgentStatusPausedBy(**workspace_pause["paused_by"])
        if workspace_pause["paused_by"] is not None
        else None
    )
    enabled = bool(role.agentic_mode_enabled)
    if enabled and workspace_pause["paused"]:
        effective_paused = True
        pause_scope: Literal["workspace", "role"] | None = "workspace"
        effective_paused_at = workspace_pause["paused_at"]
        effective_paused_reason = workspace_pause["reason"]
        effective_paused_by = workspace_paused_by
    elif enabled and role.agent_paused_at is not None:
        effective_paused = True
        pause_scope = "role"
        effective_paused_at = role.agent_paused_at
        effective_paused_reason = role.agent_paused_reason
        effective_paused_by = role_paused_by
    else:
        effective_paused = False
        pause_scope = None
        effective_paused_at = None
        effective_paused_reason = None
        effective_paused_by = None

    return AgentStatusPayload(
        role_id=role_id,
        enabled=enabled,
        can_control_agent=has_job_permission_for_role(
            db,
            current_user=current_user,
            role=role,
            permission=JobPermission.CONTROL_AGENT,
        ),
        paused=effective_paused,
        pause_scope=pause_scope,
        paused_at=effective_paused_at,
        paused_reason=effective_paused_reason,
        paused_by=effective_paused_by,
        role_paused_at=role.agent_paused_at,
        role_paused_reason=role.agent_paused_reason,
        role_paused_by=role_paused_by,
        workspace_paused=bool(workspace_pause["paused"]),
        workspace_paused_at=workspace_pause["paused_at"],
        workspace_paused_reason=workspace_pause["reason"],
        workspace_paused_by=workspace_paused_by,
        workspace_control_version=int(workspace_pause["version"]),
        last_run_at=role.agent_last_run_at,
        bootstrap_status=getattr(role, "agent_bootstrap_status", None),
        bootstrap_error=getattr(role, "agent_bootstrap_error", None),
        bootstrap_started_at=getattr(role, "agent_bootstrap_started_at", None),
        bootstrap_completed_at=getattr(role, "agent_bootstrap_completed_at", None),
        pending_decisions=pending,
        pending_breakdown=AgentStatusPendingBreakdown(
            total=pending,
            decisions=int(pending_decisions_count),
            questions=int(open_needs_input_count),
        ),
        monthly_budget_cents=role.monthly_usd_budget_cents,
        monthly_spent_cents=monthly_spent,
        current_run=current_run,
        last_activity=last_activity,
    )


@router.get("/roles/{role_id}/agent/activity", response_model=AgentActivityPayload)
def agent_activity(
    role_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reverse-chronological feed of what the agent has been doing on this role.

    Merges four sources, all already persisted by the runtime:
      * agent_runs           — cycle started/finished/failed/paused
      * agent_decisions      — what got scored and recommended
      * candidate_application_events (actor=agent) — stage moves it made
      * agent_needs_input    — questions the agent raised + their resolution

    Cursor pagination via ``before`` (ISO timestamp). ``has_more`` is a
    cheap hint — true iff any source returned exactly ``limit`` rows.
    """
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")

    entries, has_more = build_activity_feed(
        db,
        organization_id=current_user.organization_id,
        role_id=role_id,
        limit=limit,
        before=before,
    )
    return AgentActivityPayload(role_id=role_id, entries=entries, has_more=has_more)


@router.post("/roles/{role_id}/agent/run-now", response_model=RunNowResult)
def run_now(
    role_id: int,
    body: RunNowBody = Body(default_factory=RunNowBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")
    if not bool(role.agentic_mode_enabled):
        raise HTTPException(
            status_code=409,
            detail="agent is not enabled for this role",
        )
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    if bool(workspace_pause["paused"]):
        return RunNowResult(
            role_id=role_id,
            queued=False,
            blocked=True,
            pause_scope="workspace",
            detail="agent run blocked while the workspace agent is paused",
        )
    if role.agent_paused_at is not None:
        return RunNowResult(
            role_id=role_id,
            queued=False,
            blocked=True,
            pause_scope="role",
            detail=f"agent is paused: {role.agent_paused_reason or 'unspecified'}",
        )

    from ...services.role_agent_dispatch import dispatch_role_agent_cycle

    async_result = dispatch_role_agent_cycle(
        role, manual=True, application_id=body.application_id
    )
    return RunNowResult(role_id=role_id, queued=True, task_id=str(async_result.id))


# Reason stamped on a recruiter-initiated org-wide pause. Distinct from the
# orchestrator's budget reasons so the activity tick / panel copy reads as a
# deliberate pause rather than "monthly budget reached".
MANUAL_PAUSE_REASON = "paused by recruiter"


def _compensate_failed_agent_dispatch(
    db: Session,
    *,
    role_id: int,
    dispatched_version: int,
    current_user: User,
) -> None:
    """Pause a failed resume without overwriting a later recruiter action."""

    role = (
        role_query_for_update(
            db,
            role_id=role_id,
            organization_id=int(current_user.organization_id),
        )
        .populate_existing()
        .first()
    )
    # The dispatch result belongs to the state that was just resumed. If a
    # recruiter deleted, disabled, or independently paused the role after that
    # commit, their newer control is already the safe terminal state.
    if (
        role is None
        or int(role.version or 1) != int(dispatched_version)
        or not bool(role.agentic_mode_enabled)
        or role.agent_paused_at is not None
    ):
        db.commit()
        return

    compensation_before = capture_role_change_snapshot(role)
    compensation_from = int(role.version or 1)
    budget_guard.pause_role(db, role=role, reason="agent bootstrap dispatch failed")
    role.agent_bootstrap_status = "failed"
    role.agent_bootstrap_error = "agent bootstrap dispatch failed"
    role.agent_bootstrap_completed_at = datetime.now(timezone.utc)
    compensation_to = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=compensation_before,
        action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
        actor_user_id=int(current_user.id),
        from_version=compensation_from,
        to_version=compensation_to,
        reason="agent bootstrap dispatch failed",
        request_id=get_request_id(),
    )
    db.commit()


class BulkAgentPauseResult(BaseModel):
    """Outcome of a workspace bulk role-control transition."""

    affected: int  # enabled roles whose effective state changed this call
    enabled_count: int  # agent-enabled roles considered
    skipped: int = 0  # newly unblocked roles not immediately dispatched
    workspace_paused: bool
    workspace_control_version: int
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by: Optional[AgentStatusPausedBy] = None


class WorkspaceControlCommand(BaseModel):
    expected_control_version: int = Field(ge=1)


def _workspace_control_conflict(state: dict[str, Any]) -> HTTPException:
    paused_at = state["paused_at"]
    paused_by = state["paused_by"]
    last_change = state.get("last_change")
    return HTTPException(
        status_code=409,
        detail={
            "message": (
                "Workspace agent control changed after you opened this page. "
                "The latest state is shown; review it and try again."
            ),
            "current": {
                "workspace_paused": bool(state["paused"]),
                "workspace_control_version": int(state["version"]),
                "paused_at": (
                    paused_at.isoformat()
                    if isinstance(paused_at, datetime)
                    else paused_at
                ),
                "paused_reason": state["reason"],
                "paused_by": (
                    AgentStatusPausedBy(**paused_by).model_dump(mode="json")
                    if paused_by is not None
                    else None
                ),
                "changed_by": (
                    {
                        **last_change,
                        "changed_at": (
                            last_change["changed_at"].isoformat()
                            if isinstance(last_change.get("changed_at"), datetime)
                            else last_change.get("changed_at")
                        ),
                    }
                    if last_change is not None
                    else None
                ),
            },
        },
    )


@router.post("/agent/pause-all", response_model=BulkAgentPauseResult)
def pause_all_agents(
    body: WorkspaceControlCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Pause every currently-running role without creating a global blocker.

    The workspace control is a convenience bulk action. Roles already paused
    manually or by a runtime guard remain untouched, and any role paused here
    can be resumed independently from its own page.
    """
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == int(current_user.organization_id),
        )
        .with_for_update(of=Organization)
        .one()
    )
    current_state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    last_action = (current_state.get("last_change") or {}).get("action")
    if int(body.expected_control_version) != int(current_state["version"]):
        enabled_roles = db.query(Role.id).filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None), Role.agentic_mode_enabled.is_(True),
        )
        enabled_count = enabled_roles.count()
        if last_action == "paused" and enabled_roles.filter(Role.agent_paused_at.is_(None)).count() == 0:
            return BulkAgentPauseResult(
                affected=0,
                enabled_count=enabled_count,
                workspace_paused=False,
                workspace_control_version=int(current_state["version"]),
            )
        raise _workspace_control_conflict(current_state)
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .order_by(Role.id)
        .with_for_update(of=Role)
        .all()
    )
    enabled_count = len(roles)
    selected = [role for role in roles if role.agent_paused_at is None]
    affected = len(selected)
    if selected or organization.agent_workspace_paused_at is not None:
        advance_workspace_control(
            db,
            organization=organization,
            actor_user_id=int(current_user.id),
            actor_name=str(current_user.full_name or current_user.email),
            action="paused",
            reason=WORKSPACE_BULK_PAUSE_REASON,
            request_id=get_request_id(),
        )
        for role in selected:
            before = capture_role_change_snapshot(role)
            role_from_version = int(role.version or 1)
            budget_guard.pause_role(
                db, role=role, reason=WORKSPACE_BULK_PAUSE_REASON
            )
            role_to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
                actor_user_id=int(current_user.id),
                from_version=role_from_version,
                to_version=role_to_version,
                reason=WORKSPACE_BULK_PAUSE_REASON,
                request_id=get_request_id(),
            )
        db.commit()
    state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return BulkAgentPauseResult(
        affected=affected,
        enabled_count=int(enabled_count),
        # Deliberately false: there is no workspace execution overlay.
        workspace_paused=False,
        workspace_control_version=int(state["version"]),
        paused_at=state["paused_at"],
        paused_reason=state["reason"],
        paused_by=(
            AgentStatusPausedBy(**state["paused_by"])
            if state["paused_by"] is not None
            else None
        ),
    )


@router.post("/agent/resume-all", response_model=BulkAgentPauseResult)
def resume_all_agents(
    body: WorkspaceControlCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Resume every paused enabled role whose safety checks are healthy."""
    organization = (
        db.query(Organization)
        .filter(Organization.id == int(current_user.organization_id))
        .with_for_update(of=Organization)
        .one()
    )
    current_state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    last_action = (current_state.get("last_change") or {}).get("action")
    if int(body.expected_control_version) != int(current_state["version"]):
        enabled_roles = db.query(Role.id).filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None), Role.agentic_mode_enabled.is_(True),
        )
        enabled_count = enabled_roles.count()
        if last_action == "resumed" and enabled_roles.filter(Role.agent_paused_at.isnot(None)).count() == 0:
            return BulkAgentPauseResult(
                affected=0,
                enabled_count=int(enabled_count),
                workspace_paused=False,
                workspace_control_version=int(current_state["version"]),
            )
        raise _workspace_control_conflict(current_state)
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.isnot(None),
        )
        .order_by(Role.id)
        .with_for_update(of=Role)
        .all()
    )
    had_legacy_overlay = organization.agent_workspace_paused_at is not None
    enabled_count = db.query(Role.id).filter(
        Role.organization_id == int(current_user.organization_id),
        Role.deleted_at.is_(None),
        Role.agentic_mode_enabled.is_(True),
    ).count()
    if roles or had_legacy_overlay:
        advance_workspace_control(
            db,
            organization=organization,
            actor_user_id=int(current_user.id),
            actor_name=str(current_user.full_name or current_user.email),
            action="resumed",
            reason="workspace resumed by recruiter",
            request_id=get_request_id(),
        )
    resumed_roles: list[tuple[Role, int]] = []
    skipped = 0
    for role in roles:
        before = capture_role_change_snapshot(role)
        role_from_version = int(role.version or 1)
        if not budget_guard.resume_if_under_budget(db, role=role, explicit=True):
            skipped += 1
            continue
        role_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=before,
            action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
            actor_user_id=int(current_user.id),
            from_version=role_from_version,
            to_version=role_to_version,
            reason="workspace resumed by recruiter",
            request_id=get_request_id(),
        )
        resumed_roles.append((role, role_to_version))
    if roles or had_legacy_overlay:
        db.commit()

    dispatch_failed = 0
    for role, role_version in resumed_roles:
        try:
            from ...services.role_agent_dispatch import dispatch_role_agent_cycle

            dispatch_role_agent_cycle(role, role_version=role_version)
        except Exception:
            logger.exception(
                "Failed to enqueue workspace-resume cycle for role_id=%s", role.id
            )
            dispatch_failed += 1

    state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return BulkAgentPauseResult(
        affected=len(resumed_roles),
        enabled_count=int(enabled_count),
        skipped=skipped + dispatch_failed,
        workspace_paused=False,
        workspace_control_version=int(state["version"]),
        paused_at=state["paused_at"],
        paused_reason=state["reason"],
        paused_by=None,
    )


class RoleAgentPauseResult(BaseModel):
    """Outcome of a per-role manual pause / resume."""

    role_id: int
    version: int
    paused: bool  # is the role paused after this call?
    pause_scope: Optional[Literal["workspace", "role"]] = None
    resumed: bool = False  # did this call actually clear a pause?
    reason: Optional[str] = None


@router.post("/roles/{role_id}/agent/pause", response_model=RoleAgentPauseResult)
def pause_role_agent(
    role_id: int,
    body: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually soft-pause ONE role's agent — the per-role twin of pause-all.

    Sets ``agent_paused_at`` (the flag the cohort sweeps honour, so the agent
    stops scoring/spending on the next beat) while leaving
    ``agentic_mode_enabled`` true. Crucially this KEEPS the role's pending
    decisions, and ``resume`` brings it straight back. Distinct from turning
    the agent off (PATCH ``agentic_mode_enabled=false``), which stops the agent
    indefinitely and doesn't auto-resume — neither path discards the queue.
    Idempotent: pausing an already-paused role is a no-op.
    """
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
            "agentic_mode_enabled": bool(role.agentic_mode_enabled),
            "agent_paused_at": role.agent_paused_at.isoformat()
            if role.agent_paused_at
            else None,
            "agent_paused_reason": role.agent_paused_reason,
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    if not bool(role.agentic_mode_enabled):
        raise HTTPException(
            status_code=409, detail="agent is not enabled for this role"
        )
    if role.agent_paused_at is None:
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        budget_guard.pause_role(db, role=role, reason=MANUAL_PAUSE_REASON)
        audit_to = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            actor_user_id=int(current_user.id),
            from_version=audit_from,
            to_version=audit_to,
            reason=MANUAL_PAUSE_REASON,
            request_id=get_request_id(),
        )
        db.commit()
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return RoleAgentPauseResult(
        role_id=role_id,
        version=int(role.version or 1),
        paused=(
            bool(role.agentic_mode_enabled)
            and (bool(workspace_pause["paused"]) or role.agent_paused_at is not None)
        ),
        pause_scope=(
            "workspace"
            if workspace_pause["paused"]
            else ("role" if role.agent_paused_at is not None else None)
        ),
        reason=(
            workspace_pause["reason"]
            if workspace_pause["paused"]
            else role.agent_paused_reason
        ),
    )


@router.post("/roles/{role_id}/agent/resume", response_model=RoleAgentPauseResult)
def resume_role_agent(
    role_id: int,
    body: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume ONE paused role, if it's back under its monthly cap.

    Reuses ``budget_guard.resume_if_under_budget`` (the same guard as
    resume-all and the cap-raise auto-resume) so a genuinely over-budget role
    stays paused rather than resuming only to re-pause next cycle. On a real
    resume we kick an immediate review cycle so the recruiter doesn't wait up
    to 60 minutes for the next beat — mirroring the PATCH resume path.
    """
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
            "agentic_mode_enabled": bool(role.agentic_mode_enabled),
            "agent_paused_at": role.agent_paused_at.isoformat()
            if role.agent_paused_at
            else None,
            "agent_paused_reason": role.agent_paused_reason,
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    # Surface a concrete production-runtime failure on the explicit endpoint.
    # The shared budget_guard repeats this check at the mutation boundary so
    # non-HTTP resume paths fail closed too; this preflight exists to return an
    # actionable 503 instead of a misleading ``resumed=false`` when the budget
    # itself is already clear.
    if (
        bool(role.agentic_mode_enabled)
        and role.agent_paused_at is not None
        and budget_guard.check_monthly_usd(db, role=role).ok
    ):
        from ...services.agent_activation_readiness import (
            activation_readiness,
            readiness_message,
        )

        readiness = activation_readiness(role)
        if not readiness.get("ready"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Agent runtime is not ready: "
                    f"{readiness_message(readiness)}. The role remains paused."
                ),
            )
    audit_before = capture_role_change_snapshot(role)
    audit_from = int(role.version or 1)
    resumed = budget_guard.resume_if_under_budget(
        db,
        role=role,
        explicit=True,
    )
    if resumed:
        audit_to = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
            actor_user_id=int(current_user.id),
            from_version=audit_from,
            to_version=audit_to,
            reason="resume requested by recruiter",
            request_id=get_request_id(),
        )
        db.commit()
        from ...services.workspace_agent_control import (
            workspace_agent_control_snapshot,
        )

        workspace_held, _workspace_control_version = (
            workspace_agent_control_snapshot(
                db,
                organization_id=int(current_user.organization_id),
            )
        )
        if not workspace_held:
            try:
                from ...services.role_agent_dispatch import dispatch_role_agent_cycle

                dispatch_role_agent_cycle(role, role_version=int(audit_to))
            except Exception:
                logger.exception(
                    "Failed to enqueue resume cycle for role_id=%s", role.id
                )
                _compensate_failed_agent_dispatch(
                    db,
                    role_id=int(role.id),
                    dispatched_version=int(audit_to),
                    current_user=current_user,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The agent worker queue is unavailable. The role was left "
                        "paused; retry Resume."
                    ),
                )
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return RoleAgentPauseResult(
        role_id=role_id,
        version=int(role.version or 1),
        paused=(
            bool(role.agentic_mode_enabled)
            and (bool(workspace_pause["paused"]) or role.agent_paused_at is not None)
        ),
        pause_scope=(
            "workspace"
            if workspace_pause["paused"]
            else ("role" if role.agent_paused_at is not None else None)
        ),
        resumed=resumed,
        reason=(
            workspace_pause["reason"]
            if workspace_pause["paused"]
            else role.agent_paused_reason
        ),
    )
