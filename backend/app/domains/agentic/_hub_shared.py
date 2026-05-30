"""Shared helpers + pydantic schemas for the Hub route modules.

Both ``hub_routes`` (org-status / KPIs / role breakdown) and
``hub_feedback_routes`` (teach / cosign / revert / snooze / lists) share
the same time-window helpers and feedback payload builder. Pulling them
into a single private module keeps each route file under the
500-LOC architecture gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...models.agent_decision import AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.decision_feedback import DecisionFeedback
from ...models.role import Role
from ...models.user import User


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Feedback rows can be reverted within this grace window. After the window,
# the action is permanent (the agent has likely consumed the signal).
FEEDBACK_REVERT_GRACE = timedelta(hours=1)
SNOOZE_DURATION = timedelta(hours=1)
RANGE_TO_DAYS = {"24h": 1, "7d": 7, "30d": 30}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def range_start(range_label: str) -> datetime:
    days = RANGE_TO_DAYS.get(range_label, 7)
    return now_utc() - timedelta(days=days)


def start_of_day_utc() -> datetime:
    n = now_utc()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start_utc() -> datetime:
    n = now_utc()
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def short_role_name(name: Optional[str]) -> str:
    if not name:
        return ""
    parts = str(name).split()
    if len(parts) <= 2:
        return str(name)
    # "Senior Backend Engineer" → "Sr. Backend"
    head = parts[0][:3].rstrip(".") + "."
    return f"{head} {parts[1]}"


def pending_filter(now: datetime):
    """Pending = status='pending' AND (snoozed_until is null OR <= now)."""
    return and_(
        AgentDecision.status == "pending",
        or_(
            AgentDecision.snoozed_until.is_(None),
            AgentDecision.snoozed_until <= now,
        ),
    )


def open_needs_input_filter():
    """Open recruiter question = resolved_at IS NULL AND dismissed_at IS NULL.

    Used by every "pending" counter so questions the agent is asking
    show up in the same KPI as decisions the agent has made — the UI
    surfaces them in the same Review queue, so the count must too.
    """
    return and_(
        AgentNeedsInput.resolved_at.is_(None),
        AgentNeedsInput.dismissed_at.is_(None),
    )


# ---------------------------------------------------------------------------
# KPI / status payload schemas
# ---------------------------------------------------------------------------


class OrgKpiPayload(BaseModel):
    # ``pending`` is the unioned count (AgentDecision pending +
    # AgentNeedsInput open) — total user-actionable items in the
    # Review queue. ``pending_decisions`` and ``pending_questions``
    # break it down so tile-specific labels don't have to conflate
    # them ("Decisions today: X / N pending" should use the decisions-
    # only count, not the union).
    pending: int
    pending_decisions: int
    pending_questions: int
    # Snooze-aware pending decisions grouped by raw decision_type
    # (advance_to_interview / send_assessment / reject / …). Sums to
    # ``pending_decisions``; the Hub strip buckets these for display.
    pending_by_type: dict[str, int] = Field(default_factory=dict)
    today: int
    auto_applied_today: int
    org_budget_spent_cents: int
    org_budget_cap_cents: int
    override_rate_pct: float
    teach_rate_pct: float
    paused_role_count: int
    active_role_count: int
    oldest_pending_age_seconds: Optional[int]


class OrgStatusPayload(OrgKpiPayload):
    last_decision_at: Optional[datetime]


class RoleBreakdownRow(BaseModel):
    role_id: int
    name: str
    short_name: str
    pending: int
    today: int
    week: int
    # All-time decision count for the role — lets the Hub hide roles the agent
    # has never acted on from the by-role comparison table.
    decisions_total: int = 0
    budget_cents: int
    cap_cents: int
    override_rate_pct: float
    teach_rate_pct: float
    paused: bool
    paused_reason: Optional[str]
    agentic_mode_enabled: bool
    # Live candidate-pipeline standing for the role (same source the Jobs page
    # uses): {applied, invited, in_assessment, review, advanced, rejected}.
    # Lets the Hub show "already advanced N" context next to the pending queue
    # so a recruiter knows the denominator before advancing more.
    stage_counts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Feedback / teach loop schemas
# ---------------------------------------------------------------------------


class SnoozeResult(BaseModel):
    decision_id: int
    snoozed_until: datetime


class FeedbackBody(BaseModel):
    decision_id: int
    failure_mode: str = Field(min_length=1)
    correction_text: str = Field(min_length=1, max_length=8000)
    scope: str = Field(min_length=1)
    role_id: Optional[int] = None
    # v2 attribution. The v2 TeachModal always sends these; older clients
    # may omit them and the action treats them as None.
    attributed_to: Optional[str] = None
    direction: Optional[str] = None
    # List of GraphWriteHint dicts — the route delegates schema
    # validation to the writeback pipeline. We accept anything dict-like
    # here so the action can pass them through verbatim.
    graph_write_hints: Optional[list[dict]] = None


class FeedbackPayload(BaseModel):
    id: int
    decision_id: int
    reviewer_id: int
    reviewer_name: Optional[str]
    role_id: Optional[int]
    role_name: Optional[str]
    failure_mode: str
    correction_text: str
    scope: str
    attributed_to: Optional[str] = None
    direction: Optional[str] = None
    graph_write_hints: Optional[list[dict]] = None
    cosign_required: bool
    cosigned_by_user_id: Optional[int]
    cosigned_by_name: Optional[str]
    cosigned_at: Optional[datetime]
    applied_at: Optional[datetime]
    applied_revision_id: Optional[int]
    reverted_at: Optional[datetime]
    created_at: datetime
    # How many other recent same-scope decisions exist — used by the
    # frontend Signal section as context, not as a "this many will be
    # retuned" promise. Scoring/decision-making improvements are a
    # separate workstream; see docs/HOME_HUB_DESIGN.md §8.
    decisions_in_scope: int


class FeedbackCreateResult(BaseModel):
    feedback: FeedbackPayload
    decision_status: str


class CosignResult(BaseModel):
    feedback: FeedbackPayload


class RevertResult(BaseModel):
    feedback_id: int
    decision_id: int
    decision_status: str


# NOTE: ``RubricRevisionPayload`` was here but was removed when we stripped
# the rubric-revisions surface from the Hub. The model + table stay; see
# ``hub_feedback_routes.py`` for the rationale.


# ---------------------------------------------------------------------------
# Realised-outcome schema — surfaces what actually happened to candidates
# after the agent's recommendation was approved (advance → interviewed/hired,
# reject → rejected_confirmed). Sourced from ``role.agent_calibration["outcomes"]``.
# ---------------------------------------------------------------------------


class RealisedOutcomeRow(BaseModel):
    role_id: int
    role_name: Optional[str]
    decision_id: Optional[int]
    decision_type: str
    outcome: str  # interviewed | hired | rejected_confirmed
    application_id: Optional[int]
    observed_at: Optional[str]  # ISO string straight from JSON


# ---------------------------------------------------------------------------
# Feedback payload builder — shared between POST + GET feedback endpoints
# ---------------------------------------------------------------------------


def feedback_payload(
    db: Session,
    feedback: DecisionFeedback,
    *,
    organization_id: int,
) -> FeedbackPayload:
    role = (
        db.query(Role).filter(Role.id == feedback.role_id).first()
        if feedback.role_id
        else None
    )
    reviewer = (
        db.query(User).filter(User.id == feedback.reviewer_id).first()
        if feedback.reviewer_id
        else None
    )
    cosigner = (
        db.query(User).filter(User.id == feedback.cosigned_by_user_id).first()
        if feedback.cosigned_by_user_id
        else None
    )

    # Cheap count of other recent same-scope decisions — pure context for
    # the Signal section ("you've corrected n decisions on this role this
    # month"), nothing more.
    if feedback.scope == "decision":
        decisions_in_scope = 1
    else:
        from sqlalchemy import func

        q = db.query(func.count(AgentDecision.id)).filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.created_at >= now_utc() - timedelta(days=30),
        )
        if feedback.scope == "role" and feedback.role_id is not None:
            q = q.filter(AgentDecision.role_id == int(feedback.role_id))
        decisions_in_scope = int(q.scalar() or 0)

    return FeedbackPayload(
        id=int(feedback.id),
        decision_id=int(feedback.decision_id),
        reviewer_id=int(feedback.reviewer_id),
        reviewer_name=getattr(reviewer, "full_name", None) if reviewer else None,
        role_id=int(feedback.role_id) if feedback.role_id else None,
        role_name=str(role.name) if role else None,
        failure_mode=str(feedback.failure_mode),
        correction_text=str(feedback.correction_text),
        scope=str(feedback.scope),
        attributed_to=feedback.attributed_to,
        direction=feedback.direction,
        graph_write_hints=feedback.graph_write_hints,
        cosign_required=bool(feedback.cosign_required),
        cosigned_by_user_id=(
            int(feedback.cosigned_by_user_id) if feedback.cosigned_by_user_id else None
        ),
        cosigned_by_name=getattr(cosigner, "full_name", None) if cosigner else None,
        cosigned_at=feedback.cosigned_at,
        applied_at=feedback.applied_at,
        applied_revision_id=(
            int(feedback.applied_revision_id) if feedback.applied_revision_id else None
        ),
        reverted_at=feedback.reverted_at,
        created_at=feedback.created_at,
        decisions_in_scope=decisions_in_scope,
    )


__all__ = [
    "FEEDBACK_REVERT_GRACE",
    "SNOOZE_DURATION",
    "RANGE_TO_DAYS",
    "now_utc",
    "range_start",
    "start_of_day_utc",
    "month_start_utc",
    "short_role_name",
    "pending_filter",
    "open_needs_input_filter",
    "OrgKpiPayload",
    "OrgStatusPayload",
    "RoleBreakdownRow",
    "SnoozeResult",
    "FeedbackBody",
    "FeedbackPayload",
    "FeedbackCreateResult",
    "CosignResult",
    "RevertResult",
    "RealisedOutcomeRow",
    "feedback_payload",
]
