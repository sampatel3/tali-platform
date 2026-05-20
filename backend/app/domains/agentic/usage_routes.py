"""Per-role usage breakdown — backs the Role budget panel.

Same monthly window the budget guard uses, grouped by feature so
recruiters can see whether their cap is going to scoring, pre-screen,
semantic search, the agent, etc.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import get_current_user
from ...models.role import Role
from ...models.usage_event import UsageEvent
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic"])


class RoleUsageBreakdownLine(BaseModel):
    feature: str
    label: str
    cost_cents: int
    event_count: int


class RoleUsageBreakdown(BaseModel):
    role_id: int
    role_name: str
    monthly_budget_cents: int
    monthly_spent_cents: int
    month_start: datetime
    by_feature: list[RoleUsageBreakdownLine]


# Pre-screen and CV parsing share a recruiter mental model ("the platform
# looked at the CV"); scoring rolls in the related pairwise/archetype/rerank
# passes that produce the Taali score. Semantic search is graph_sync.
# Assessments include interview prep + analysis. Anything else → "Other".
_FEATURE_LABELS = {
    "prescreen":           "Pre-screen",
    "cv_parse":            "Pre-screen",
    "score":               "Scoring",
    "cv_rerank":           "Scoring",
    "archetype_synthesis": "Scoring",
    "pairwise_judge":      "Scoring",
    "fit_matching":        "Scoring",
    "graph_sync":          "Semantic search",
    "assessment":          "Assessments",
    "interview_focus":     "Assessments",
    "interview_tech":      "Assessments",
    "agent_autonomous":    "Agent",
    "taali_chat":          "Chat",
    "search_parse":        "Chat",
    "other":               "Other",
}


@router.get("/roles/{role_id}/usage/breakdown", response_model=RoleUsageBreakdown)
def role_usage_breakdown(
    role_id: int,
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

    now = datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    rows = (
        db.query(
            UsageEvent.feature,
            func.coalesce(func.sum(UsageEvent.credits_charged), 0).label("cost_micro"),
            func.count(UsageEvent.id).label("event_count"),
        )
        .filter(
            UsageEvent.organization_id == current_user.organization_id,
            UsageEvent.role_id == role_id,
            UsageEvent.created_at >= month_start,
        )
        .group_by(UsageEvent.feature)
        .all()
    )

    lines = [
        RoleUsageBreakdownLine(
            feature=str(r.feature),
            label=_FEATURE_LABELS.get(
                str(r.feature),
                str(r.feature).replace("_", " ").title(),
            ),
            cost_cents=int(int(r.cost_micro or 0) / 10_000),
            event_count=int(r.event_count or 0),
        )
        for r in rows
    ]
    lines.sort(key=lambda l: l.cost_cents, reverse=True)

    return RoleUsageBreakdown(
        role_id=role_id,
        role_name=str(role.name or ""),
        monthly_budget_cents=budget_guard.role_monthly_usd_cents(role),
        monthly_spent_cents=budget_guard.month_to_date_spend_cents(db, role=role),
        month_start=month_start,
        by_feature=lines,
    )
