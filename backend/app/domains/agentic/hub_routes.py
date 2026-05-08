"""HTTP routes that back the agent-first ``/home`` page (the "Hub").

Read-side only: org-wide poll, KPI strip, per-role breakdown, realised
outcomes. The teach loop (snooze / feedback / cosign / revert / lists)
lives in the sibling ``hub_feedback_routes`` module so each file stays
under the 500-LOC architecture gate.

  GET  /agent/org-status                  org-wide poll for the live tab badge
  GET  /agent/kpis                        KPI strip
  GET  /agent/roles/breakdown             per-role table
  GET  /agent/realised-outcomes           the "world says" loop in SIGNAL

All endpoints are org-scoped via ``get_current_user``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from ._hub_shared import (
    OrgKpiPayload,
    OrgStatusPayload,
    RANGE_TO_DAYS,
    RealisedOutcomeRow,
    RoleBreakdownRow,
    month_start_utc,
    now_utc,
    pending_filter,
    short_role_name,
    start_of_day_utc,
)
from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.role import Role
from ...models.usage_event import UsageEvent
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic-hub"])


# ---------------------------------------------------------------------------
# KPI computation — shared between /agent/org-status and /agent/kpis
# ---------------------------------------------------------------------------


def _compute_kpis(db: Session, *, organization_id: int, range_days: int = 7) -> OrgKpiPayload:
    now = now_utc()
    today_start = start_of_day_utc()
    range_start = now - timedelta(days=range_days)
    month_start = month_start_utc()

    # Pending (snooze-aware) + oldest pending age.
    pending_q = db.query(AgentDecision).filter(
        AgentDecision.organization_id == organization_id,
        pending_filter(now),
    )
    pending = pending_q.count()
    oldest_pending_row = pending_q.order_by(AgentDecision.created_at.asc()).first()
    oldest_pending_age = (
        int((now - oldest_pending_row.created_at).total_seconds())
        if oldest_pending_row is not None and oldest_pending_row.created_at is not None
        else None
    )

    # Decisions today (created_at >= start of day).
    today = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.created_at >= today_start,
        )
        .count()
    )

    # Auto-applied today: resolved without a human disposition (system-driven).
    # Today every action is recruiter-driven so this is 0 — kept so the KPI
    # strip can show "n auto-applied" once autonomy lands.
    auto_applied_today = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.resolved_at >= today_start,
            AgentDecision.human_disposition.is_(None),
            AgentDecision.status.in_(("approved", "overridden")),
        )
        .count()
    )

    # Override + teach rate over the rolling window. Denominator is decisions
    # *resolved by a human* in the window — pure auto-applied actions don't
    # count toward "did the human disagree."
    resolved_q = (
        db.query(
            func.count(AgentDecision.id),
            func.sum(case((AgentDecision.human_disposition == "overridden", 1), else_=0)),
            func.sum(case((AgentDecision.human_disposition == "taught", 1), else_=0)),
        )
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.resolved_at >= range_start,
            AgentDecision.human_disposition.in_(("approved", "overridden", "taught")),
        )
    )
    total_resolved, total_overridden, total_taught = resolved_q.one() or (0, 0, 0)
    total_resolved = int(total_resolved or 0)
    override_pct = (
        (float(total_overridden or 0) / float(total_resolved)) * 100.0
        if total_resolved
        else 0.0
    )
    teach_pct = (
        (float(total_taught or 0) / float(total_resolved)) * 100.0
        if total_resolved
        else 0.0
    )

    # Org budget = sum of role caps + sum of MTD usage_events for the org.
    role_rows = (
        db.query(
            Role.id,
            Role.monthly_usd_budget_cents,
            Role.agentic_mode_enabled,
            Role.agent_paused_at,
        )
        .filter(
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .all()
    )
    cap_cents = sum(int(r.monthly_usd_budget_cents or 0) for r in role_rows)
    active_role_count = sum(
        1 for r in role_rows if bool(r.agentic_mode_enabled) and r.agent_paused_at is None
    )
    paused_role_count = sum(
        1 for r in role_rows if bool(r.agentic_mode_enabled) and r.agent_paused_at is not None
    )

    spent_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0))
        .filter(
            UsageEvent.organization_id == organization_id,
            UsageEvent.created_at >= month_start,
        )
        .scalar()
        or 0
    )
    spent_cents = int(int(spent_micro) / 10_000)

    return OrgKpiPayload(
        pending=int(pending),
        today=int(today),
        auto_applied_today=int(auto_applied_today),
        org_budget_spent_cents=int(spent_cents),
        org_budget_cap_cents=int(cap_cents),
        override_rate_pct=round(override_pct, 1),
        teach_rate_pct=round(teach_pct, 1),
        paused_role_count=int(paused_role_count),
        active_role_count=int(active_role_count),
        oldest_pending_age_seconds=oldest_pending_age,
    )


# ---------------------------------------------------------------------------
# GET /agent/org-status
# ---------------------------------------------------------------------------


@router.get("/agent/org-status", response_model=OrgStatusPayload)
def org_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Org-wide poll — drives the live tab badge and Hub KPI strip.

    Scoped to ``current_user.organization_id``. Cheap enough to poll on a
    30-second cadence: counts + sums only.
    """
    base = _compute_kpis(db, organization_id=current_user.organization_id, range_days=7)
    last_decision = (
        db.query(AgentDecision.created_at)
        .filter(AgentDecision.organization_id == current_user.organization_id)
        .order_by(desc(AgentDecision.created_at))
        .limit(1)
        .scalar()
    )
    return OrgStatusPayload(
        **base.model_dump(),
        last_decision_at=last_decision,
    )


# ---------------------------------------------------------------------------
# GET /agent/kpis
# ---------------------------------------------------------------------------


@router.get("/agent/kpis", response_model=OrgKpiPayload)
def agent_kpis(
    range: str = Query(default="7d"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if range not in RANGE_TO_DAYS:
        raise HTTPException(status_code=422, detail=f"unsupported range={range!r}")
    return _compute_kpis(
        db,
        organization_id=current_user.organization_id,
        range_days=RANGE_TO_DAYS[range],
    )


# ---------------------------------------------------------------------------
# GET /agent/roles/breakdown
# ---------------------------------------------------------------------------


@router.get("/agent/roles/breakdown", response_model=list[RoleBreakdownRow])
def roles_breakdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = now_utc()
    today_start = start_of_day_utc()
    week_start = now - timedelta(days=7)
    month_start = month_start_utc()

    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .all()
    )

    # Pre-compute per-role aggregates in three single queries to avoid
    # N+1 across the role list.
    pending_by_role = dict(
        db.query(AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            pending_filter(now),
        )
        .group_by(AgentDecision.role_id)
        .all()
    )
    today_by_role = dict(
        db.query(AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.created_at >= today_start,
        )
        .group_by(AgentDecision.role_id)
        .all()
    )
    week_by_role = dict(
        db.query(AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.created_at >= week_start,
        )
        .group_by(AgentDecision.role_id)
        .all()
    )
    spend_by_role = dict(
        db.query(UsageEvent.role_id, func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0))
        .filter(
            UsageEvent.organization_id == current_user.organization_id,
            UsageEvent.created_at >= month_start,
            UsageEvent.role_id.isnot(None),
        )
        .group_by(UsageEvent.role_id)
        .all()
    )

    # Override / teach rates per role over the last 7d.
    disposition_rows = (
        db.query(
            AgentDecision.role_id,
            func.count(AgentDecision.id),
            func.sum(case((AgentDecision.human_disposition == "overridden", 1), else_=0)),
            func.sum(case((AgentDecision.human_disposition == "taught", 1), else_=0)),
        )
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.resolved_at >= week_start,
            AgentDecision.human_disposition.in_(("approved", "overridden", "taught")),
        )
        .group_by(AgentDecision.role_id)
        .all()
    )
    disposition_by_role = {
        rid: (int(total or 0), int(ovr or 0), int(tch or 0))
        for rid, total, ovr, tch in disposition_rows
    }

    rows: list[RoleBreakdownRow] = []
    for role in roles:
        rid = int(role.id)
        total, ovr, tch = disposition_by_role.get(rid, (0, 0, 0))
        ovr_pct = (ovr / total * 100.0) if total else 0.0
        tch_pct = (tch / total * 100.0) if total else 0.0
        spent_micro = int(spend_by_role.get(rid, 0) or 0)
        rows.append(
            RoleBreakdownRow(
                role_id=rid,
                name=str(role.name or ""),
                short_name=short_role_name(role.name),
                pending=int(pending_by_role.get(rid, 0)),
                today=int(today_by_role.get(rid, 0)),
                week=int(week_by_role.get(rid, 0)),
                budget_cents=int(spent_micro / 10_000),
                cap_cents=int(role.monthly_usd_budget_cents or 0),
                override_rate_pct=round(ovr_pct, 1),
                teach_rate_pct=round(tch_pct, 1),
                paused=role.agent_paused_at is not None,
                paused_reason=role.agent_paused_reason,
                agentic_mode_enabled=bool(role.agentic_mode_enabled),
            )
        )
    # Sort: pending desc, then name asc (so the most-needs-attention rows lead).
    rows.sort(key=lambda r: (-r.pending, r.name))
    return rows


# ---------------------------------------------------------------------------
# GET /agent/realised-outcomes
#
# Surfaces what actually happened to candidates downstream of the agent's
# approved decisions. Distinct from teach feedback — this is the "world
# says agent was right or wrong" loop, sourced from
# ``role.agent_calibration["outcomes"]`` (see outcome_learning.py).
# ---------------------------------------------------------------------------


@router.get("/agent/realised-outcomes", response_model=list[RealisedOutcomeRow])
def realised_outcomes(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .all()
    )

    flat: list[RealisedOutcomeRow] = []
    for role in roles:
        outcomes = (role.agent_calibration or {}).get("outcomes") or []
        for entry in outcomes:
            if not isinstance(entry, dict):
                continue
            flat.append(
                RealisedOutcomeRow(
                    role_id=int(role.id),
                    role_name=str(role.name) if role.name else None,
                    decision_id=(
                        int(entry["decision_id"]) if entry.get("decision_id") else None
                    ),
                    decision_type=str(entry.get("decision_type", "")),
                    outcome=str(entry.get("outcome", "")),
                    application_id=(
                        int(entry["application_id"])
                        if entry.get("application_id")
                        else None
                    ),
                    observed_at=str(entry["observed_at"])
                    if entry.get("observed_at")
                    else None,
                )
            )
    # Newest first by observed_at (ISO strings sort lexically).
    flat.sort(key=lambda r: (r.observed_at or ""), reverse=True)
    return flat[:limit]
