"""Per-job budget enforcement.

One layer left, after a long compaction:

**Monthly USD cap** (universal). Sum ``UsageEvent.credits_charged`` across
*all* features (scoring, pre-screen, assessment, agent) for the role in
the current calendar month and refuse if over
``role.monthly_usd_budget_cents``. Pauses the role on hit and is
checked from every Anthropic-spending entry point that has role context
— when agentic mode is on, this budget covers everything the platform
does for that role, not just the agent. This is the *only* condition
that should ever auto-pause a role.

Removed history (all in 2026-05):
- per-cycle TOKEN gate (default 50k) — redundant with MAX_TOOL_ROUNDS,
  fired on legitimate large cohorts, and worst of all leaked into
  ``pause_role`` so any single cycle that ran a bit long permanently
  disabled the role.
- per-cycle DECISION gate (default 20) — intended as "pacing" so the
  reviewer's queue wouldn't get blasted with 100 decisions at once, but
  in practice meant a role with 400 candidates needed dozens of daily
  cron cycles to clear. Pacing is a UI concern, not an orchestrator
  one. Trust the monthly $ cap as the only spending guard;
  MAX_TOOL_ROUNDS bounds runaway loops.

Org-level credit balance is enforced separately by the existing
``usage_metering_service`` ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.usage_event import UsageEvent


DEFAULT_USD_BUDGET_MONTHLY_CENTS = 5_000  # $50.00


@dataclass(frozen=True)
class BudgetCheck:
    ok: bool
    reason: Optional[str] = None


def month_start() -> datetime:
    """First day of the current month at 00:00 **UTC** — the single canonical
    month boundary every budget/usage surface must measure spend over. Use this
    everywhere; do NOT recompute a month start from local time."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _month_start_utc(now: datetime) -> datetime:
    # Back-compat shim — prefer the no-arg ``month_start()``.
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def micro_to_cents(micro) -> int:
    """The one micro-credits → cents conversion (1 cent = 10_000 micro-credits).
    Every budget/usage cents figure goes through here so units never diverge."""
    return int(int(micro or 0) / 10_000)


def role_monthly_usd_cents(role: Role) -> int:
    return int(role.monthly_usd_budget_cents or DEFAULT_USD_BUDGET_MONTHLY_CENTS)


def spend_by_role_map(db: Session, *, organization_id: int) -> dict:
    """``{role_id: MTD spend cents}`` for the org, EXCLUDING ``role_id IS NULL``.

    The single definition the per-role agent cards and the org rollup share, so
    they reconcile by construction (``org spend == Σ card spends``). Same
    basis/window/unit as ``month_to_date_spend_cents``."""
    rows = (
        db.query(
            UsageEvent.role_id,
            func.coalesce(func.sum(UsageEvent.credits_charged), 0),
        )
        .filter(
            UsageEvent.organization_id == organization_id,
            UsageEvent.role_id.isnot(None),
            UsageEvent.created_at >= month_start(),
        )
        .group_by(UsageEvent.role_id)
        .all()
    )
    return {int(rid): micro_to_cents(micro) for rid, micro in rows}


def org_month_to_date_spend_cents(db: Session, *, organization_id: int) -> int:
    """Org MTD spend in cents = Σ per-role *attributed* spend (EXCLUDES
    ``role_id IS NULL``), so the org budget tile reconciles exactly with the
    sum of the per-role job cards. Unattributed spend (graph_sync
    candidate-indexing, etc.) is org overhead surfaced on the Usage tab — it is
    NOT charged against the per-role caps, whose denominator (Σ role budgets)
    has no bucket for it."""
    spent_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.credits_charged), 0))
        .filter(
            UsageEvent.organization_id == organization_id,
            UsageEvent.role_id.isnot(None),
            UsageEvent.created_at >= month_start(),
        )
        .scalar()
        or 0
    )
    return micro_to_cents(spent_micro)


def month_to_date_spend_cents(db: Session, *, role: Role) -> int:
    """Month-to-date charged spend on this role, in cents.

    Aggregates ``UsageEvent.credits_charged`` (raw Anthropic cost ×
    per-feature markup) across every feature where ``role_id`` matches.
    Same unit as ``Role.monthly_usd_budget_cents`` so the cap check and
    every customer-facing display reconcile.
    """
    spent_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.credits_charged), 0))
        .filter(
            UsageEvent.organization_id == role.organization_id,
            UsageEvent.role_id == role.id,
            UsageEvent.created_at >= month_start(),
        )
        .scalar()
        or 0
    )
    return micro_to_cents(spent_micro)


def month_to_date_raw_cost_cents(db: Session, *, role: Role) -> int:
    """Month-to-date RAW Anthropic cost on this role, in cents.

    Aggregates ``UsageEvent.cost_usd_micro`` (the pre-markup Anthropic cost)
    over the SAME window/filter as ``month_to_date_spend_cents``. The
    difference between the two is Taali's margin on this role for the month —
    surfaced so the budget panel can show Anthropic cost vs charged credits
    rather than only the marked-up number the cap is denominated in.

    Excludes ``cache_hit`` rows: a cache hit is served from cv_score_cache and
    makes NO Anthropic call, so its real Anthropic cost is $0 regardless of
    cost_usd_micro. Post-#476 those rows already carry cost_usd_micro=0, but
    pre-#476 rows still in the MTD window populated the full cached cost — which
    inflated the raw-cost total (and understated margin) by up to ~60%. The
    cache FEE stays in ``month_to_date_spend_cents`` (credits), so the margin
    correctly counts it as margin.
    """
    raw_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0))
        .filter(
            UsageEvent.organization_id == role.organization_id,
            UsageEvent.role_id == role.id,
            UsageEvent.created_at >= month_start(),
            UsageEvent.cache_hit == 0,
        )
        .scalar()
        or 0
    )
    return micro_to_cents(raw_micro)


def check_monthly_usd(db: Session, *, role: Role) -> BudgetCheck:
    """Refuse when month-to-date spend on the role hits its cap.

    Universal: covers scoring + pre-screen + assessment + agent spend
    on this role, not just the autonomous agent.
    """
    cap_cents = role_monthly_usd_cents(role)
    if cap_cents <= 0:
        return BudgetCheck(ok=True)  # 0 means unset
    spent_cents = month_to_date_spend_cents(db, role=role)
    if spent_cents >= cap_cents:
        return BudgetCheck(
            ok=False,
            reason=f"monthly USD cap reached: {spent_cents}c >= {cap_cents}c",
        )
    return BudgetCheck(ok=True)


def pause_role(db: Session, *, role: Role, reason: str) -> None:
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = reason
    db.add(role)


def resume_if_under_budget(db: Session, *, role: Role) -> bool:
    """Clear a budget-triggered pause once the role is back under its cap.

    The inverse of :func:`pause_role`. The monthly USD cap is the *only*
    thing that auto-pauses a role (see module docstring), so a paused but
    still agent-enabled role is by definition budget-paused. When the
    recruiter raises ``monthly_usd_budget_cents`` above month-to-date
    spend, the role should resume on its own — the cohort sweep skips
    paused roles (``agent_paused_at IS NULL``), so without this the raised
    cap has no effect until the recruiter manually toggles the agent
    off/on.

    Acts only when the role is still agent-enabled (a manually disabled
    agent stays off), currently paused, and *now genuinely under the cap*
    — so the next cycle won't immediately re-pause and emit a confusing
    pause event. Returns ``True`` when a pause was actually cleared, so the
    caller can kick an immediate cycle instead of waiting for the beat.
    """
    if role.agent_paused_at is None or not bool(role.agentic_mode_enabled):
        return False
    if not check_monthly_usd(db, role=role).ok:
        return False
    role.agent_paused_at = None
    role.agent_paused_reason = None
    db.add(role)
    return True
