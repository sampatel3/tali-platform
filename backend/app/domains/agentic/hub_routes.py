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

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from ._hub_shared import (
    OrgKpiPayload,
    OrgStatusPayload,
    RANGE_TO_DAYS,
    RealisedOutcomeRow,
    RoleBreakdownRow,
    now_utc,
    org_header_extras,
    pending_filter,
    role_pending_decisions_by_type_bulk,
    short_role_name,
    start_of_day_utc,
)
from ...agent_runtime import budget_guard
from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.organization import Organization
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.user import User
from ...platform.database import get_db
from ...services.decision_membership import LiveLogicalDecisionScope, resolve_live_logical_decision_scope
from ...services.needs_input_membership import apply_live_logical_needs_input_scope
from ...services.sister_role_service import related_role_pipeline_counts_bulk
from ..assessments_runtime.pipeline_service import role_pipeline_counts_bulk


router = APIRouter(tags=["agentic-hub"])
# ---------------------------------------------------------------------------
# KPI computation — shared between /agent/org-status and /agent/kpis
# ---------------------------------------------------------------------------


def _compute_kpis(
    db: Session,
    *,
    organization_id: int,
    range_days: int = 7,
    decision_scope: LiveLogicalDecisionScope | None = None,
) -> OrgKpiPayload:
    now = now_utc()
    today_start = start_of_day_utc()
    range_start = now - timedelta(days=range_days)
    decision_scope = decision_scope or resolve_live_logical_decision_scope(
        db,
        organization_id=int(organization_id),
    )
    # "Awaiting you" combines every snooze-aware live decision with open
    # role-level questions; paused-role decisions remain actionable.
    pending_decisions_q = decision_scope.query(db, AgentDecision).filter(
        pending_filter(now)
    )
    pending_decisions = pending_decisions_q.count()
    # The type split uses the identical slice so it reconciles with the total.
    pending_by_type = {
        str(dt): int(c)
        for dt, c in decision_scope.query(
            db,
            AgentDecision.decision_type,
            func.count(AgentDecision.id),
        )
        .filter(pending_filter(now))
        .group_by(AgentDecision.decision_type)
        .all()
    }
    questions_q = apply_live_logical_needs_input_scope(
        db, db.query(AgentNeedsInput), organization_id=int(organization_id)
    )
    pending_questions = questions_q.filter(
        AgentNeedsInput.resolved_at.is_(None),
        AgentNeedsInput.dismissed_at.is_(None),
    ).count()
    pending = int(pending_decisions) + int(pending_questions)
    oldest_pending_row = pending_decisions_q.order_by(AgentDecision.created_at.asc()).first()
    oldest_pending_age = None
    if oldest_pending_row is not None and oldest_pending_row.created_at is not None:
        # Postgres returns tz-aware; the SQLite test DB returns naive. Coerce
        # so a single naive row can't 500 the KPI strip (mirrors the guard in
        # routes._decision_to_payload).
        created = oldest_pending_row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        oldest_pending_age = max(0, int((now - created).total_seconds()))

    # Decisions today (created_at >= start of day).
    today = decision_scope.query(db, AgentDecision).filter(
        AgentDecision.created_at >= today_start
    ).count()

    # Resolved without a human disposition (reserved for future autonomy).
    auto_applied_today = (
        decision_scope.query(db, AgentDecision)
        .filter(
            AgentDecision.resolved_at >= today_start,
            AgentDecision.human_disposition.is_(None),
            AgentDecision.status.in_(("approved", "overridden")),
        )
        .count()
    )

    # Denominator is only decisions resolved by a human in this window.
    resolved_q = decision_scope.query(
        db,
        func.count(AgentDecision.id),
        func.sum(case((AgentDecision.human_disposition == "overridden", 1), else_=0)),
        func.sum(case((AgentDecision.human_disposition == "taught", 1), else_=0)),
    ).filter(
            AgentDecision.resolved_at >= range_start,
            AgentDecision.human_disposition.in_(("approved", "overridden", "taught")),
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
    local_paused_role_count = sum(
        1 for r in role_rows if bool(r.agentic_mode_enabled) and r.agent_paused_at is not None
    )
    enabled_role_count = int(active_role_count) + int(local_paused_role_count)
    workspace_paused = (
        db.query(Organization.id)
        .filter(
            Organization.id == int(organization_id),
            Organization.agent_workspace_paused_at.isnot(None),
        )
        .first()
        is not None
    )
    if workspace_paused:
        active_role_count = 0
        paused_role_count = enabled_role_count
    else:
        paused_role_count = local_paused_role_count

    # Customer-facing org spend = Tali charged credits (raw Anthropic cost ×
    # per-feature markup). Canonical helper EXCLUDES role_id IS NULL so the org
    # tile == Σ of the per-role job cards (whose cap denominator has no bucket
    # for unattributed spend). Unattributed/overhead spend surfaces on the
    # Usage tab, not against the per-role caps. One unit across every surface.
    spent_cents = budget_guard.org_month_to_date_spend_cents(
        db, organization_id=organization_id
    )

    return OrgKpiPayload(
        pending=int(pending),
        pending_decisions=int(pending_decisions),
        pending_questions=int(pending_questions),
        pending_by_type=pending_by_type,
        today=int(today),
        auto_applied_today=int(auto_applied_today),
        org_budget_spent_cents=int(spent_cents),
        org_budget_cap_cents=int(cap_cents),
        override_rate_pct=round(override_pct, 1),
        teach_rate_pct=round(teach_pct, 1),
        paused_role_count=int(paused_role_count),
        active_role_count=int(active_role_count),
        local_paused_role_count=int(local_paused_role_count),
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
    org_id = current_user.organization_id
    decision_scope = resolve_live_logical_decision_scope(
        db,
        organization_id=int(org_id),
    )
    base = _compute_kpis(
        db,
        organization_id=org_id,
        range_days=7,
        decision_scope=decision_scope,
    )
    last_decision = (
        decision_scope.query(db, AgentDecision.created_at)
        .order_by(desc(AgentDecision.created_at))
        .limit(1)
        .scalar()
    )
    # Additive header fields (current_run / last_activity / paused_reason) for
    # the global AgentBar — all org-scoped aggregates so the bar can drop its
    # /roles + per-role /agent/status fan-out. Computed in _hub_shared.
    extras = org_header_extras(
        db,
        organization_id=org_id,
        current_user_id=int(current_user.id),
    )

    return OrgStatusPayload(
        **base.model_dump(),
        last_decision_at=last_decision,
        **extras,
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
    decision_scope = resolve_live_logical_decision_scope(
        db,
        organization_id=int(current_user.organization_id),
    )
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .all()
    )

    # Live candidate-pipeline standing per role (advanced / review / rejected
    # …) — the same source the Jobs page uses — so the Hub can show "already
    # advanced N" context alongside the pending queue. Batched to two queries.
    stage_counts_by_role = role_pipeline_counts_bulk(
        db,
        organization_id=current_user.organization_id,
        role_ids=[int(r.id) for r in roles],
    )
    stage_counts_by_role.update(related_role_pipeline_counts_bulk(
        db,
        [
            int(role.id)
            for role in roles
            if role.role_kind == ROLE_KIND_SISTER
            or role.ats_owner_role_id is not None
        ],
        organization_id=int(current_user.organization_id),
    ))
    # Per-role pending decisions grouped by type — feeds the funnel's
    # "awaiting your decision" chips when the home funnel is scoped to a role.
    pending_by_type_by_role = role_pending_decisions_by_type_bulk(
        db,
        organization_id=current_user.organization_id,
        role_ids=[int(r.id) for r in roles],
        now=now,
        decision_scope=decision_scope,
    )

    # Batched per-role decision aggregates avoid N+1 reads.
    pending_by_role = dict(
        decision_scope.query(db, AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(pending_filter(now))
        .group_by(AgentDecision.role_id)
        .all()
    )
    today_by_role = dict(
        decision_scope.query(db, AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(AgentDecision.created_at >= today_start)
        .group_by(AgentDecision.role_id)
        .all()
    )
    week_by_role = dict(
        decision_scope.query(db, AgentDecision.role_id, func.count(AgentDecision.id))
        .filter(AgentDecision.created_at >= week_start)
        .group_by(AgentDecision.role_id)
        .all()
    )
    total_by_role = dict(
        decision_scope.query(db, AgentDecision.role_id, func.count(AgentDecision.id))
        .group_by(AgentDecision.role_id)
        .all()
    )
    # Same canonical per-role MTD spend definition used by the org rollup.
    spend_cents_by_role = budget_guard.spend_by_role_map(
        db, organization_id=current_user.organization_id
    )

    # Override / teach rates per role over the last 7d.
    disposition_rows = (
        decision_scope.query(
            db,
            AgentDecision.role_id,
            func.count(AgentDecision.id),
            func.sum(
                case(
                    (AgentDecision.human_disposition == "overridden", 1),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (AgentDecision.human_disposition == "taught", 1),
                    else_=0,
                )
            ),
        )
        .filter(
            AgentDecision.resolved_at >= week_start,
            AgentDecision.human_disposition.in_(
                ("approved", "overridden", "taught")
            ),
        )
        .group_by(AgentDecision.role_id)
        .all()
    )
    disposition_by_role = {
        rid: (int(total or 0), int(ovr or 0), int(tch or 0))
        for rid, total, ovr, tch in disposition_rows
    }

    workspace_pause = db.query(Organization).filter(
        Organization.id == int(current_user.organization_id)
    ).one()
    workspace_held = workspace_pause.agent_workspace_paused_at is not None

    rows: list[RoleBreakdownRow] = []
    for role in roles:
        rid = int(role.id)
        total, ovr, tch = disposition_by_role.get(rid, (0, 0, 0))
        ovr_pct = (ovr / total * 100.0) if total else 0.0
        tch_pct = (tch / total * 100.0) if total else 0.0
        role_spent_cents = int(spend_cents_by_role.get(rid, 0) or 0)
        rows.append(
            RoleBreakdownRow(
                role_id=rid,
                name=str(role.name or ""),
                short_name=short_role_name(role.name),
                pending=int(pending_by_role.get(rid, 0)),
                today=int(today_by_role.get(rid, 0)),
                week=int(week_by_role.get(rid, 0)),
                decisions_total=int(total_by_role.get(rid, 0)),
                budget_cents=role_spent_cents,
                cap_cents=int(role.monthly_usd_budget_cents or 0),
                override_rate_pct=round(ovr_pct, 1),
                teach_rate_pct=round(tch_pct, 1),
                paused=bool(role.agentic_mode_enabled)
                and (workspace_held or role.agent_paused_at is not None),
                paused_reason=(
                    workspace_pause.agent_workspace_paused_reason
                    if workspace_held
                    else role.agent_paused_reason
                ),
                pause_scope=(
                    "workspace"
                    if bool(role.agentic_mode_enabled) and workspace_held
                    else (
                        "role"
                        if bool(role.agentic_mode_enabled) and role.agent_paused_at is not None
                        else None
                    )
                ),
                role_paused=role.agent_paused_at is not None,
                agentic_mode_enabled=bool(role.agentic_mode_enabled),
                stage_counts=stage_counts_by_role.get(rid, {}),
                pending_decisions_by_type=pending_by_type_by_role.get(rid, {}),
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
