"""Shared builder for the agent activity feed.

One merged, reverse-chronological stream over four already-persisted
sources:

  * agent_runs                       — cycle started/finished/failed/paused
  * agent_decisions                  — what got recommended (+ candidate)
  * candidate_application_events     — stage moves the agent made
  * agent_needs_input                — questions raised + their resolution

Used by both the per-role feed (``GET /roles/{id}/agent/activity`` in
``routes.py``) and the org-wide feed (``GET /agent/activity`` in
``hub_panel_routes.py``). Pass ``role_id=None`` for the org-wide variant;
each entry then carries ``role_id``/``role_name`` so the UI can label the
row with which role it came from.

Kept out of ``routes.py`` so neither route file carries a duplicate copy
of the merge logic (and so the org-wide feed can't drift from the
per-role one).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ...models.agent_decision import AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...services.decision_membership import apply_live_logical_decision_scope
from ...services.logical_event_membership import apply_live_logical_event_scope


class AgentActivityEntry(BaseModel):
    # Unified shape for the activity feed. ``kind`` discriminates the
    # source (run / decision / event / needs_input) so the UI can pick
    # the right icon + verb without each row carrying a switchful payload.
    kind: str
    id: int
    created_at: datetime
    title: str
    detail: Optional[str] = None
    actor_type: Optional[str] = None
    application_id: Optional[int] = None
    candidate_name: Optional[str] = None
    status: Optional[str] = None
    decision_type: Optional[str] = None
    confidence: Optional[float] = None
    cost_micro_usd: Optional[int] = None
    # Populated only for the org-wide feed so the row can show its role.
    role_id: Optional[int] = None
    role_name: Optional[str] = None


class AgentActivityPayload(BaseModel):
    role_id: int
    entries: list[AgentActivityEntry]
    has_more: bool


class OrgActivityPayload(BaseModel):
    entries: list[AgentActivityEntry]
    has_more: bool


def confidence_to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_DECISION_VERB = {
    "advance_to_interview": "Recommended advance",
    "reject": "Recommended reject",
    "skip_assessment_reject": "Recommended reject at pre-screen",
    "send_assessment": "Recommended send assessment",
    "resend_assessment_invite": "Recommended resend assessment",
    "escalate_low_confidence": "Escalated — low confidence",
}

# Human labels for agent run triggers and needs-input kinds — recruiters
# see these in the activity feed, so map internal codes to plain words.
_TRIGGER_LABEL = {
    "cron": "scheduled",
    "manual": "started manually",
    "event": "new activity",
}

_NEED_KIND_LABEL = {
    "intent_slot_missing": "Role setup question",
    "intent_clarification": "Role intent question",
    "monthly_budget_missing": "Budget not set",
    "threshold_ambiguous": "Score threshold question",
    "task_assignment_missing": "No assessment linked",
    "candidate_tie_break": "Candidate tie-break",
    "missing_job_spec": "Missing job description",
    "missing_cv": "Missing CV",
    "cv_unreadable": "Unreadable CV",
    "confirm_material_change": "Job spec changed",
    "other": "Question",
}

_CYCLE_FAILED_DETAIL = (
    "Something went wrong during this cycle. The agent will pick this up "
    "again on its next run."
)


def build_activity_feed(
    db: Session,
    *,
    organization_id: int,
    role_id: Optional[int] = None,
    limit: int = 50,
    before: Optional[datetime] = None,
) -> tuple[list[AgentActivityEntry], bool]:
    """Merge the four activity sources into one reverse-chron feed.

    Over-fetches ``limit`` rows from each source, merges, sorts newest-
    first, then trims to ``limit``. ``has_more`` is a cheap hint — true
    iff any single source returned a full page.
    """
    fetch_n = limit

    runs_q = db.query(AgentRun).filter(AgentRun.organization_id == organization_id)
    decisions_q = apply_live_logical_decision_scope(
        db,
        db.query(AgentDecision, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id),
        organization_id=int(organization_id),
    )
    events_q = apply_live_logical_event_scope(
        db,
        db.query(
            CandidateApplicationEvent,
            Candidate,
            CandidateApplicationEvent.role_id,
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(CandidateApplicationEvent.actor_type == "agent"),
        organization_id=int(organization_id),
    )
    needs_q = db.query(AgentNeedsInput).filter(
        AgentNeedsInput.organization_id == organization_id
    )

    if role_id is not None:
        runs_q = runs_q.filter(AgentRun.role_id == role_id)
        decisions_q = decisions_q.filter(AgentDecision.role_id == role_id)
        # Event.role_id is the immutable logical role that owns the action.
        # The joined application may belong to an ATS transport/owner role for
        # a related-role member, so it is only used to resolve the candidate.
        events_q = events_q.filter(CandidateApplicationEvent.role_id == role_id)
        needs_q = needs_q.filter(AgentNeedsInput.role_id == role_id)

    if before is not None:
        runs_q = runs_q.filter(AgentRun.started_at < before)
        decisions_q = decisions_q.filter(AgentDecision.created_at < before)
        events_q = events_q.filter(CandidateApplicationEvent.created_at < before)
        needs_q = needs_q.filter(AgentNeedsInput.created_at < before)

    runs = runs_q.order_by(desc(AgentRun.started_at)).limit(fetch_n).all()
    decisions = decisions_q.order_by(desc(AgentDecision.created_at)).limit(fetch_n).all()
    events = events_q.order_by(desc(CandidateApplicationEvent.created_at)).limit(fetch_n).all()
    needs = needs_q.order_by(desc(AgentNeedsInput.created_at)).limit(fetch_n).all()

    # Resolve role names once for the org-wide feed (per-role feed knows
    # its role already, so skip the lookup).
    role_names: dict[int, str] = {}
    if role_id is None:
        role_names = dict(
            db.query(Role.id, Role.name).filter(Role.organization_id == organization_id).all()
        )

    def _role_name(rid: Optional[int]) -> Optional[str]:
        if role_id is not None or rid is None:
            return None
        return role_names.get(int(rid))

    entries: list[AgentActivityEntry] = []

    for run in runs:
        if run.status == "running":
            trigger_label = _TRIGGER_LABEL.get(str(run.trigger), str(run.trigger))
            title = f"Cycle started ({trigger_label})"
        elif run.status == "succeeded":
            n = int(run.decisions_emitted or 0)
            title = f"Cycle finished — {n} decision{'s' if n != 1 else ''}"
        elif run.status == "budget_paused":
            title = "Cycle paused — budget"
        elif run.status == "failed":
            title = "Cycle failed"
        elif run.status == "aborted":
            title = "Cycle aborted"
        else:
            title = f"Cycle · {run.status}"
        detail = _CYCLE_FAILED_DETAIL if run.status in ("failed", "aborted") else None
        entries.append(
            AgentActivityEntry(
                kind="run",
                id=int(run.id),
                created_at=run.started_at,
                title=title,
                detail=detail,
                actor_type="agent",
                status=str(run.status),
                cost_micro_usd=int(run.total_cost_micro_usd or 0),
                role_id=int(run.role_id) if run.role_id is not None else None,
                role_name=_role_name(run.role_id),
            )
        )

    for decision, candidate in decisions:
        cand_name = getattr(candidate, "full_name", None) if candidate else None
        verb = _DECISION_VERB.get(
            str(decision.decision_type),
            str(decision.decision_type).replace("_", " ").title(),
        )
        title = f"{verb} · {cand_name}" if cand_name else verb
        entries.append(
            AgentActivityEntry(
                kind="decision",
                id=int(decision.id),
                created_at=decision.created_at,
                title=title,
                detail=(decision.reasoning or "")[:240] or None,
                actor_type="agent",
                application_id=int(decision.application_id),
                candidate_name=cand_name,
                status=str(decision.status),
                decision_type=str(decision.decision_type),
                confidence=confidence_to_float(decision.confidence),
                role_id=int(decision.role_id) if decision.role_id is not None else None,
                role_name=_role_name(decision.role_id),
            )
        )

    for event, candidate, ev_role_id in events:
        cand_name = getattr(candidate, "full_name", None) if candidate else None
        parts: list[str] = []
        if event.from_stage and event.to_stage:
            parts.append(f"{event.from_stage} → {event.to_stage}")
        elif event.to_stage:
            parts.append(f"→ {event.to_stage}")
        if event.to_outcome and event.to_outcome != event.from_outcome:
            parts.append(event.to_outcome)
        moved = ", ".join(parts) if parts else str(event.event_type)
        title = f"{moved} · {cand_name}" if cand_name else moved
        entries.append(
            AgentActivityEntry(
                kind="event",
                id=int(event.id),
                created_at=event.created_at,
                title=title,
                detail=event.reason or None,
                actor_type=str(event.actor_type),
                application_id=int(event.application_id),
                candidate_name=cand_name,
                status=str(event.event_type),
                role_id=int(ev_role_id) if ev_role_id is not None else None,
                role_name=_role_name(ev_role_id),
            )
        )

    for need in needs:
        if need.resolved_at is not None:
            title_prefix = "Question answered"
        elif need.dismissed_at is not None:
            title_prefix = "Question dismissed"
        else:
            title_prefix = "Needs your input"
        entries.append(
            AgentActivityEntry(
                kind="needs_input",
                id=int(need.id),
                created_at=need.created_at,
                title=f"{title_prefix} · {_NEED_KIND_LABEL.get(str(need.kind), str(need.kind).replace('_', ' ').title())}",
                detail=(need.prompt or "")[:240] or None,
                actor_type="agent",
                status=(
                    "resolved" if need.resolved_at is not None
                    else "dismissed" if need.dismissed_at is not None
                    else "open"
                ),
                role_id=int(need.role_id) if need.role_id is not None else None,
                role_name=_role_name(need.role_id),
            )
        )

    entries.sort(key=lambda e: e.created_at, reverse=True)
    has_more = (
        len(runs) >= fetch_n
        or len(decisions) >= fetch_n
        or len(events) >= fetch_n
        or len(needs) >= fetch_n
    )
    return entries[:limit], has_more
