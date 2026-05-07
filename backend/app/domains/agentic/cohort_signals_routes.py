"""HTTP surface for cohort signals — the "do high scorers cluster?" view.

Single endpoint:

  GET /api/v1/roles/{role_id}/agent/cohort-signals

Returns the cached payload on ``role.agent_cohort_signals`` if it's
fresh, otherwise computes and caches. ``?force_recompute=true`` skips
the cache. The agent's get_cohort_signals tool uses the same caching
logic; this endpoint exists so recruiters (via the role detail page)
can see the same numbers the agent reasons over.

Org-scoped via ``get_current_user`` like every other agentic route.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...services import cohort_signals_service


router = APIRouter(tags=["agentic"])


# Same TTL the agent uses (kept in sync via the import). Updated whenever
# the agent's tool dispatcher changes its cache window.
COHORT_SIGNALS_TTL = timedelta(hours=1)


class CohortSignalEntry(BaseModel):
    feature: str
    top_freq: float
    rest_freq: float
    lift: Optional[float] = Field(
        None,
        description="None when the feature is exclusive to top scorers (lift is +inf).",
    )
    exclusive_to_top: bool
    top_n: int
    rest_n: int


class CohortSignalsCategories(BaseModel):
    skills: list[CohortSignalEntry] = Field(default_factory=list)
    companies: list[CohortSignalEntry] = Field(default_factory=list)
    titles: list[CohortSignalEntry] = Field(default_factory=list)
    schools: list[CohortSignalEntry] = Field(default_factory=list)


class CohortSignalsResponse(BaseModel):
    role_id: int
    pool_size: int
    top_size: int
    top_threshold_score: Optional[float] = None
    insufficient_data: bool
    min_pool_size: Optional[int] = None
    signals: CohortSignalsCategories
    summary: str = Field(
        "",
        description="Human-readable rendering, suitable for a dashboard panel.",
    )
    computed_at: datetime
    from_cache: bool


def _is_fresh(role: Role, *, now: Optional[datetime] = None) -> bool:
    cached = role.agent_cohort_signals
    cached_at = role.agent_cohort_signals_at
    if not isinstance(cached, dict) or cached_at is None:
        return False
    # Postgres returns aware datetimes; SQLite (in tests) returns naive
    # ones for ``DateTime(timezone=True)``. Normalize so the subtraction
    # is consistent regardless of dialect.
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    return (moment - cached_at) < COHORT_SIGNALS_TTL


def _build_response(*, role_id: int, payload: dict[str, Any], from_cache: bool) -> CohortSignalsResponse:
    raw_signals = payload.get("signals") or {}
    signals = CohortSignalsCategories(
        skills=[CohortSignalEntry(**s) for s in raw_signals.get("skills") or []],
        companies=[CohortSignalEntry(**s) for s in raw_signals.get("companies") or []],
        titles=[CohortSignalEntry(**s) for s in raw_signals.get("titles") or []],
        schools=[CohortSignalEntry(**s) for s in raw_signals.get("schools") or []],
    )
    summary = cohort_signals_service.render_summary_for_prompt(payload)
    computed_raw = payload.get("computed_at")
    if isinstance(computed_raw, str):
        try:
            computed_at = datetime.fromisoformat(computed_raw)
        except ValueError:
            computed_at = datetime.now(timezone.utc)
    elif isinstance(computed_raw, datetime):
        computed_at = computed_raw
    else:
        computed_at = datetime.now(timezone.utc)

    return CohortSignalsResponse(
        role_id=role_id,
        pool_size=int(payload.get("pool_size") or 0),
        top_size=int(payload.get("top_size") or 0),
        top_threshold_score=payload.get("top_threshold_score"),
        insufficient_data=bool(payload.get("insufficient_data", False)),
        min_pool_size=payload.get("min_pool_size"),
        signals=signals,
        summary=summary,
        computed_at=computed_at,
        from_cache=from_cache,
    )


@router.get(
    "/roles/{role_id}/agent/cohort-signals",
    response_model=CohortSignalsResponse,
)
def get_cohort_signals(
    role_id: int,
    force_recompute: bool = Query(
        default=False,
        description="Skip the 1-hour cache and recompute now. Use sparingly.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return cached or fresh "do high scorers cluster" signals for the role."""
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

    if not force_recompute and _is_fresh(role):
        cached = role.agent_cohort_signals or {}
        return _build_response(role_id=int(role.id), payload=cached, from_cache=True)

    payload = cohort_signals_service.compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(current_user.organization_id)
    )
    role.agent_cohort_signals = payload
    role.agent_cohort_signals_at = datetime.now(timezone.utc)
    db.add(role)
    db.commit()
    return _build_response(role_id=int(role.id), payload=payload, from_cache=False)
