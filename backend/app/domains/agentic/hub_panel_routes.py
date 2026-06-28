"""Aggregate read endpoints for the Settings → Background jobs "Agents" view.

  GET  /agent/panel       one round-trip: pulse, KPIs, per-agent cards,
                          24h time-series, decisions-by-type, recent decisions
  GET  /agent/activity    org-wide merged activity feed (paginated)

Deliberately non-sensitive: this surface shows agent *state* and *activity*
plus billed spend vs. cap (the same ``credits_charged`` figure already shown
on the Jobs budget panel). It never exposes raw Anthropic cost, model names,
internal feature/tool labels, or per-call metering — those stay in the
internal monitor, not a recruiter-facing page.

All endpoints org-scoped via ``get_current_user``. Lives in its own module
to keep ``hub_routes.py`` under the 500-LOC architecture gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ._activity_feed import AgentActivityEntry, OrgActivityPayload, build_activity_feed
from ._hub_shared import (
    month_start_utc,
    now_utc,
    pending_filter,
    start_of_day_utc,
)
from .hub_routes import _compute_kpis
from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...agent_runtime import budget_guard
from ...models.cv_score_job import SCORE_JOB_PENDING, CvScoreJob
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic-hub"])

# Cycle statuses the time-series + KPI strip count as a hard error (vs.
# budget_paused, which is a normal pause, not a failure).
_ERROR_STATUSES = ("failed", "aborted")
_TS_HOURS = 24


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PanelPulse(BaseModel):
    last_cycle_at: Optional[datetime]
    last_activity_at: Optional[datetime]


class PanelKpis(BaseModel):
    agents_running: int
    agents_paused: int
    pending: int
    pending_decisions: int
    decisions_today: int
    cycles_24h: int
    errors_24h: int
    budget_spent_cents: int
    budget_cap_cents: int
    oldest_pending_age_seconds: Optional[int]


class AgentLiveActivity(BaseModel):
    # ``label`` is one of WORKING / IDLE / PAUSED. ``text`` is a short,
    # IP-safe description — never tool names or model internals.
    label: str
    text: str


class AgentCard(BaseModel):
    role_id: int
    name: str
    running: bool
    paused_reason: Optional[str]
    paused_at: Optional[datetime]
    budget_spent_cents: int
    budget_cap_cents: int
    last_run_at: Optional[datetime]
    pending: int
    cycles_24h: int
    activity: AgentLiveActivity


class TimeseriesPayload(BaseModel):
    labels: list[str]
    cycles: list[int]
    decisions: list[int]
    errors: list[int]


class DecisionTypeCount(BaseModel):
    decision_type: str
    count: int


class RecentDecisionRow(BaseModel):
    id: int
    created_at: datetime
    role_id: int
    role_name: Optional[str]
    decision_type: str
    recommendation: str
    status: str
    candidate_name: Optional[str]


class AgentPanelPayload(BaseModel):
    pulse: PanelPulse
    kpis: PanelKpis
    agents: list[AgentCard]
    timeseries: TimeseriesPayload
    decisions_by_type: list[DecisionTypeCount]
    recent_decisions: list[RecentDecisionRow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to tz-aware UTC.

    Postgres returns tz-aware values; the SQLite test DB returns naive
    ones. Normalising here keeps the bucketing math correct on both.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _hour_buckets(now: datetime) -> list[datetime]:
    """24 hour-truncated buckets, oldest first, ending at the current hour."""
    top = now.replace(minute=0, second=0, microsecond=0)
    return [top - timedelta(hours=_TS_HOURS - 1 - i) for i in range(_TS_HOURS)]


def _bucket_counts(timestamps: list[datetime], buckets: list[datetime]) -> list[int]:
    first = buckets[0]
    counts = [0] * len(buckets)
    for ts in timestamps:
        ts = _as_utc(ts)
        if ts is None:
            continue
        idx = int((ts - first).total_seconds() // 3600)
        if 0 <= idx < len(counts):
            counts[idx] += 1
    return counts


def _live_activity(
    *,
    paused: bool,
    paused_reason: Optional[str],
    running_rounds: Optional[int],
    is_running_cycle: bool,
    scoring_pending: int,
) -> AgentLiveActivity:
    if paused:
        return AgentLiveActivity(label="PAUSED", text=paused_reason or "budget cap reached")
    if is_running_cycle:
        text = "reasoning cycle"
        if running_rounds:
            text = f"reasoning cycle · round {int(running_rounds)}"
        return AgentLiveActivity(label="WORKING", text=text)
    if scoring_pending > 0:
        plural = "s" if scoring_pending != 1 else ""
        return AgentLiveActivity(label="WORKING", text=f"scoring {scoring_pending} candidate{plural}")
    return AgentLiveActivity(label="IDLE", text="idle")


# ---------------------------------------------------------------------------
# GET /agent/panel
# ---------------------------------------------------------------------------


@router.get("/agent/panel", response_model=AgentPanelPayload)
def agent_panel(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    now = now_utc()
    today_start = start_of_day_utc()
    month_start = month_start_utc()
    window_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=_TS_HOURS - 1)

    base = _compute_kpis(db, organization_id=org_id, range_days=7)

    # --- per-role aggregates (single query each, no N+1) ------------------
    pending_by_role = dict(
        db.query(AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(AgentDecision.organization_id == org_id, pending_filter(now))
        .group_by(AgentDecision.role_id)
        .all()
    )
    cycles_by_role = dict(
        db.query(AgentRun.role_id, func.count(AgentRun.id))
        .filter(AgentRun.organization_id == org_id, AgentRun.started_at >= now - timedelta(hours=24))
        .group_by(AgentRun.role_id)
        .all()
    )
    # Canonical per-role MTD spend (cents) — one definition shared with the org
    # rollup, so the agent cards and the workspace tile reconcile.
    spend_cents_by_role = budget_guard.spend_by_role_map(db, organization_id=org_id)
    running_rounds_by_role = dict(
        db.query(AgentRun.role_id, AgentRun.rounds_executed)
        .filter(AgentRun.organization_id == org_id, AgentRun.status == "running")
        .all()
    )
    scoring_pending_by_role = dict(
        db.query(CvScoreJob.role_id, func.count(CvScoreJob.id))
        .join(Role, Role.id == CvScoreJob.role_id)
        .filter(Role.organization_id == org_id, CvScoreJob.status == SCORE_JOB_PENDING)
        .group_by(CvScoreJob.role_id)
        .all()
    )

    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .all()
    )

    agents: list[AgentCard] = []
    for role in roles:
        rid = int(role.id)
        paused = role.agent_paused_at is not None
        role_spent_cents = int(spend_cents_by_role.get(rid, 0) or 0)
        agents.append(
            AgentCard(
                role_id=rid,
                name=str(role.name or ""),
                running=not paused,
                paused_reason=role.agent_paused_reason,
                paused_at=role.agent_paused_at,
                budget_spent_cents=role_spent_cents,
                budget_cap_cents=int(role.monthly_usd_budget_cents or 0),
                last_run_at=role.agent_last_run_at,
                pending=int(pending_by_role.get(rid, 0)),
                cycles_24h=int(cycles_by_role.get(rid, 0)),
                activity=_live_activity(
                    paused=paused,
                    paused_reason=role.agent_paused_reason,
                    running_rounds=running_rounds_by_role.get(rid),
                    is_running_cycle=rid in running_rounds_by_role,
                    scoring_pending=int(scoring_pending_by_role.get(rid, 0)),
                ),
            )
        )
    # Most-active first: running before paused, then more pending on top.
    agents.sort(key=lambda a: (not a.running, -a.pending, a.name))

    # --- 24h time-series (bucketed in Python for SQLite/Postgres parity) --
    buckets = _hour_buckets(now)
    labels = [b.strftime("%H:00") for b in buckets]
    cycle_ts = [
        r[0]
        for r in db.query(AgentRun.started_at)
        .filter(AgentRun.organization_id == org_id, AgentRun.started_at >= window_start)
        .all()
    ]
    error_ts = [
        r[0]
        for r in db.query(AgentRun.started_at)
        .filter(
            AgentRun.organization_id == org_id,
            AgentRun.started_at >= window_start,
            AgentRun.status.in_(_ERROR_STATUSES),
        )
        .all()
    ]
    decision_ts = [
        r[0]
        for r in db.query(AgentDecision.created_at)
        .filter(AgentDecision.organization_id == org_id, AgentDecision.created_at >= window_start)
        .all()
    ]
    timeseries = TimeseriesPayload(
        labels=labels,
        cycles=_bucket_counts(cycle_ts, buckets),
        decisions=_bucket_counts(decision_ts, buckets),
        errors=_bucket_counts(error_ts, buckets),
    )

    cycles_24h = len(cycle_ts)
    errors_24h = len(error_ts)

    # --- decisions by type (today) ---------------------------------------
    decisions_by_type = [
        DecisionTypeCount(decision_type=str(dt), count=int(c))
        for dt, c in (
            db.query(AgentDecision.decision_type, func.count(AgentDecision.id))
            .filter(AgentDecision.organization_id == org_id, AgentDecision.created_at >= today_start)
            .group_by(AgentDecision.decision_type)
            .order_by(desc(func.count(AgentDecision.id)))
            .all()
        )
    ]

    # --- recent decisions (decision log) ---------------------------------
    role_names = dict(db.query(Role.id, Role.name).filter(Role.organization_id == org_id).all())
    recent_rows = (
        db.query(AgentDecision, Candidate)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(AgentDecision.organization_id == org_id)
        .order_by(desc(AgentDecision.created_at))
        .limit(12)
        .all()
    )
    recent_decisions = [
        RecentDecisionRow(
            id=int(d.id),
            created_at=d.created_at,
            role_id=int(d.role_id),
            role_name=role_names.get(int(d.role_id)),
            decision_type=str(d.decision_type),
            recommendation=str(d.recommendation),
            status=str(d.status),
            candidate_name=getattr(cand, "full_name", None) if cand else None,
        )
        for d, cand in recent_rows
    ]

    last_cycle_at = (
        db.query(func.max(AgentRun.started_at))
        .filter(AgentRun.organization_id == org_id)
        .scalar()
    )
    last_decision_at = (
        db.query(func.max(AgentDecision.created_at))
        .filter(AgentDecision.organization_id == org_id)
        .scalar()
    )
    last_activity_at = max(
        (_as_utc(t) for t in (last_cycle_at, last_decision_at) if t is not None),
        default=None,
    )

    return AgentPanelPayload(
        pulse=PanelPulse(last_cycle_at=last_cycle_at, last_activity_at=last_activity_at),
        kpis=PanelKpis(
            agents_running=base.active_role_count,
            agents_paused=base.paused_role_count,
            pending=base.pending,
            pending_decisions=base.pending_decisions,
            decisions_today=base.today,
            cycles_24h=cycles_24h,
            errors_24h=errors_24h,
            budget_spent_cents=base.org_budget_spent_cents,
            budget_cap_cents=base.org_budget_cap_cents,
            oldest_pending_age_seconds=base.oldest_pending_age_seconds,
        ),
        agents=agents,
        timeseries=timeseries,
        decisions_by_type=decisions_by_type,
        recent_decisions=recent_decisions,
    )


# ---------------------------------------------------------------------------
# GET /agent/activity — org-wide merged feed
# ---------------------------------------------------------------------------


@router.get("/agent/activity", response_model=OrgActivityPayload)
def org_activity(
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entries, has_more = build_activity_feed(
        db,
        organization_id=current_user.organization_id,
        role_id=None,
        limit=limit,
        before=before,
    )
    return OrgActivityPayload(entries=entries, has_more=has_more)
