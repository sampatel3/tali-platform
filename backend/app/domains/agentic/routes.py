"""HTTP routes for the autonomous recruiting agent.

  GET    /api/v1/agent-decisions                  list pending (or any-status) decisions
  POST   /api/v1/agent-decisions/{id}/approve     execute the agent's recommendation
  POST   /api/v1/agent-decisions/{id}/override    discard recommendation; recruiter acts manually
  POST   /api/v1/agent-decisions/discard          bulk discard pending decisions for a role (used by toggle-off)
  GET    /api/v1/agent-runs                       recent autonomous-cycle log
  POST   /api/v1/roles/{id}/agent/run-now         enqueue a manual agent cycle

All endpoints are org-scoped via ``get_current_user``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from ...actions import approve_decision as approve_decision_action
from ...actions import override_decision as override_decision_action
from ...actions.types import Actor
from ...agent_runtime import budget_guard
from ...deps import get_current_user
from ...domains.assessments_runtime.role_support import is_resolved
from ...services.cv_score_orchestrator import supersede_pending_decisions_for_app
from ...models.agent_decision import AGENT_DECISION_STATUSES, AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ._activity_feed import (
    AgentActivityPayload,
    build_activity_feed,
    confidence_to_float,
)


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


# Filter shorthand: a single ``?type=advance`` lets the recruiter scope the
# Hub to every forward-progress decision the agent emits (interview, send
# assessment, resend assessment invite). Without this, the dropdown would
# need three separate options for what reads as one concept. ``reject`` and
# ``skip_assessment_reject`` stay 1:1 with their underlying decision_type —
# the Hub draws a hard visual line between post- and pre-screen rejections,
# and recruiters want to filter on that distinction.
DECISION_TYPE_CATEGORIES: dict[str, list[str]] = {
    "advance": ["advance_to_interview", "send_assessment", "resend_assessment_invite"],
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
    # The candidate's headline Tali score, 0–100. Resolved by preferring the
    # score the agent stamped on this decision's evidence (frozen at decision
    # time, present even when the application's score cache is still "pending"),
    # then the application's cached score; within each, Tali composite then
    # role-fit (== Tali pre-assessment). The Hub renders it as a score ring on
    # the card. Null for pre-screen rejects — surfaced before any scoring runs,
    # so there's no score to show.
    taali_score: Optional[float] = None
    # Workable shortcode (= role.workable_job_id) so the home-page modal
    # can fetch this role's Workable stages for the Advance / Skip & advance
    # stage <select> without a second round-trip.
    workable_job_id: Optional[str] = None
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


class RunNowBody(BaseModel):
    application_id: Optional[int] = None


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


class AgentStatusPayload(BaseModel):
    role_id: int
    enabled: bool
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    last_run_at: Optional[datetime] = None
    pending_decisions: int
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


def _first_score(*candidates: Any) -> Optional[float]:
    """Return the first candidate that is a finite number, as a float.

    Uses an explicit None/finite check rather than ``a or b`` so a genuine
    ``0.0`` score isn't skipped as falsy.
    """
    for c in candidates:
        if c is None:
            continue
        try:
            f = float(c)
        except (TypeError, ValueError):
            continue
        if f == f and f not in (float("inf"), float("-inf")):  # exclude NaN/inf
            return f
    return None


def _decision_to_payload(
    decision: AgentDecision,
    candidate: Optional[Candidate],
    role: Optional[Role] = None,
    *,
    application: Optional[CandidateApplication] = None,
    is_stale: bool = False,
    staleness_reasons: Optional[list[str]] = None,
    staleness_summary: Optional[str] = None,
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

    taali_score = None
    # Pre-screen rejects (skip_assessment_reject) are surfaced before the
    # candidate is assessed — they have no meaningful Tali score, and the card
    # must never show one. Gate on the decision type rather than on a null
    # cache: an application can carry a stale cached score from another flow
    # (e.g. a CV match) even though this decision is a pre-screen reject, and
    # that score must not leak onto the card.
    if str(decision.decision_type) != "skip_assessment_reject":
        # Prefer the score the agent stamped on THIS decision (frozen at
        # decision time, so it matches the reasoning text and is present even
        # when the application's score cache is still "pending"), then fall
        # back to the application's cached score. Within each source prefer the
        # composite Tali score, then role-fit (== Tali pre-assessment).
        evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
        taali_score = _first_score(
            evidence.get("taali_score"),
            evidence.get("role_fit_score"),
            getattr(application, "taali_score_cache_100", None) if application else None,
            getattr(application, "role_fit_score_cache_100", None) if application else None,
        )

    return AgentDecisionPayload(
        id=int(decision.id),
        role_id=int(decision.role_id),
        application_id=int(decision.application_id),
        agent_run_id=int(decision.agent_run_id) if decision.agent_run_id else None,
        decision_type=str(decision.decision_type),
        recommendation=str(decision.recommendation),
        status=str(decision.status),
        reasoning=str(decision.reasoning),
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
        taali_score=taali_score,
        workable_job_id=getattr(role, "workable_job_id", None) if role else None,
        is_stale=is_stale,
        staleness_reasons=staleness_reasons or [],
        staleness_summary=staleness_summary,
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
    status: str = Query(default="pending"),
    decision_type: Optional[str] = Query(default=None, alias="type"),
    q: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if status not in AGENT_DECISION_STATUSES and status != "all":
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
    if status != "all":
        if status == "pending":
            # The Hub queue shows actionable (pending) rows AND in-flight
            # (processing) ones — the latter rendered greyed/at-bottom so a
            # recruiter can't double-approve while the background batch runs.
            query = query.filter(
                AgentDecision.status.in_(("pending", "processing"))
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
    query = query.order_by(desc(AgentDecision.created_at)).limit(limit)
    rows = query.all()

    # A2: compute staleness per row. Only meaningful for ``pending``
    # rows (resolved decisions are frozen snapshots per A6); for
    # resolved/discarded/expired we skip the computation and return
    # is_stale=False. Done in-loop because each row needs the
    # application + role context; the staleness service handles
    # caching internally and reuses already-loaded entities when
    # callers pass them in.
    from ...services import decision_staleness
    # NOTE: CandidateApplication is already imported at module scope. Do
    # NOT re-import it here — a function-local import makes Python treat
    # the name as a local for the WHOLE function, so the earlier query
    # reference (the join above) raises UnboundLocalError at runtime.

    # Batch-load applications for ALL rows we're returning (not just pending)
    # so we can both compute staleness for pending rows AND surface the
    # candidate's role-fit score on every card, without a round-trip per row.
    all_app_ids = [int(decision.application_id) for decision, _, _ in rows]
    apps_by_id: dict[int, CandidateApplication] = {}
    if all_app_ids:
        for app in (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(all_app_ids))
            .all()
        ):
            apps_by_id[int(app.id)] = app

    # One cache for the whole page: pending rows in a queue typically share
    # a handful of roles, so this collapses the per-role criteria/note
    # lookups from O(rows) to O(distinct roles).
    staleness_cache = decision_staleness.StalenessCache()

    payloads: list[AgentDecisionPayload] = []
    for decision, candidate, role in rows:
        app = apps_by_id.get(int(decision.application_id))
        is_stale = False
        reasons: list[str] = []
        summary: Optional[str] = None
        if decision.status == "pending":
            try:
                report = decision_staleness.evaluate(
                    db, decision, application=app, role=role,
                    cache=staleness_cache,
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
                is_stale=is_stale,
                staleness_reasons=reasons,
                staleness_summary=summary,
            )
        )
    return payloads


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
    if pre_decision is not None and pre_decision.status == "pending" and not force:
        from ...services import decision_staleness
        try:
            report = decision_staleness.evaluate(db, pre_decision)
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
    return _decision_to_payload(decision, candidate, role, application=application)


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
    side_effects: dict = {}
    try:
        decision = override_decision_action.run(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_id=decision_id,
            override_action=body.override_action,
            note=body.note,
            workable_target_stage=body.workable_target_stage,
            collect_side_effects=side_effects,
        )
        db.commit()
        db.refresh(decision)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"override failed: {exc}")

    # Slow best-effort side effects run off the request path (see approve).
    _enqueue_decision_side_effects(
        decision.id,
        workable_target_stage=body.workable_target_stage,
        reject_notify=bool(side_effects.get("reject_notify", False)),
    )

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
    return _decision_to_payload(decision, candidate, role, application=application)


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
    if role is not None and role.agent_paused_at is None and bool(role.agentic_mode_enabled):
        from ...tasks.agent_tasks import agent_manual_run

        async_result = agent_manual_run.delay(
            role_id=int(decision.role_id),
            application_id=int(decision.application_id),
        )
        queued = True
        task_id = str(async_result.id)
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
    role = (
        db.query(Role)
        .filter(
            Role.id == body.role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {body.role_id} not found")

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
    result = approve_decision_action.enqueue_batch(
        db,
        Actor.recruiter(current_user),
        organization_id=current_user.organization_id,
        decision_ids=requested,
        note=note,
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

    return AgentStatusPayload(
        role_id=role_id,
        enabled=bool(role.agentic_mode_enabled),
        paused_at=role.agent_paused_at,
        paused_reason=role.agent_paused_reason,
        last_run_at=role.agent_last_run_at,
        pending_decisions=pending,
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
    if role.agent_paused_at is not None:
        return RunNowResult(
            role_id=role_id,
            queued=False,
            detail=f"agent is paused: {role.agent_paused_reason or 'unspecified'}",
        )

    from ...tasks.agent_tasks import agent_manual_run

    async_result = agent_manual_run.delay(role_id=role_id, application_id=body.application_id)
    return RunNowResult(role_id=role_id, queued=True, task_id=str(async_result.id))
