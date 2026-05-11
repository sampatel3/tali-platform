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
from ...models.agent_decision import AGENT_DECISION_STATUSES, AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic"])


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
    # Governance: evidence-validation outcome stamped by queue_decision.
    validation_status: Optional[str] = None
    validation_failures: Optional[list[str]] = None


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


class OverrideBody(BaseModel):
    override_action: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)


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


def _confidence_to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decision_to_payload(decision: AgentDecision, candidate: Optional[Candidate]) -> AgentDecisionPayload:
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
        confidence=_confidence_to_float(decision.confidence),
        model_version=str(decision.model_version),
        prompt_version=str(decision.prompt_version),
        created_at=decision.created_at,
        resolved_at=decision.resolved_at,
        resolved_by_user_id=decision.resolved_by_user_id,
        resolution_note=decision.resolution_note,
        override_action=decision.override_action,
        candidate_name=getattr(candidate, "name", None) if candidate else None,
        candidate_email=getattr(candidate, "email", None) if candidate else None,
        validation_status=decision.validation_status,
        validation_failures=(
            list(decision.validation_failures)
            if isinstance(decision.validation_failures, list)
            else None
        ),
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
        db.query(AgentDecision, Candidate)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(AgentDecision.organization_id == current_user.organization_id)
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    if status != "all":
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
        query = query.filter(AgentDecision.decision_type == decision_type)
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
    return [_decision_to_payload(decision, candidate) for decision, candidate in rows]


# ---------------------------------------------------------------------------
# POST /agent-decisions/{id}/approve
# ---------------------------------------------------------------------------


@router.post("/agent-decisions/{decision_id}/approve", response_model=AgentDecisionPayload)
def approve(
    decision_id: int,
    body: ApproveBody = Body(default_factory=ApproveBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        decision = approve_decision_action.run(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_id=decision_id,
            note=body.note,
        )
        db.commit()
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
    return _decision_to_payload(decision, candidate)


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
    try:
        decision = override_decision_action.run(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_id=decision_id,
            override_action=body.override_action,
            note=body.note,
        )
        db.commit()
        db.refresh(decision)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"override failed: {exc}")

    candidate = (
        db.query(Candidate)
        .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
        .filter(CandidateApplication.id == decision.application_id)
        .first()
    )
    return _decision_to_payload(decision, candidate)


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
    approved: int
    failures: list[BulkApproveFailure] = Field(default_factory=list)


@router.post("/agent-decisions/bulk-approve", response_model=BulkApproveResult)
def bulk_approve(
    body: BulkApproveBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve a list of pending decisions one-by-one.

    Org-scoped: the action layer rejects decisions outside the user's
    organization. Each decision is approved in its own try/except so a
    single bad row (already-resolved, missing application, etc.) doesn't
    halt the batch — callers get a per-failure summary.
    """
    requested = list(dict.fromkeys(int(x) for x in body.decision_ids))  # de-dupe, preserve order
    note = (body.note or "").strip() or None
    approved = 0
    failures: list[BulkApproveFailure] = []
    actor = Actor.recruiter(current_user)
    for decision_id in requested:
        try:
            approve_decision_action.run(
                db,
                actor,
                organization_id=current_user.organization_id,
                decision_id=decision_id,
                note=note,
            )
            db.commit()
            approved += 1
        except HTTPException as exc:
            db.rollback()
            failures.append(
                BulkApproveFailure(
                    decision_id=decision_id,
                    error=str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
                )
            )
        except Exception as exc:  # noqa: BLE001 — record + continue, never halt the batch
            db.rollback()
            failures.append(
                BulkApproveFailure(decision_id=decision_id, error=str(exc)[:300])
            )
    return BulkApproveResult(
        requested=len(requested), approved=approved, failures=failures
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
    pending = (
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
            candidate_name=getattr(candidate, "name", None) if candidate else None,
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
