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


def _month_start_utc(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def role_monthly_usd_cents(role: Role) -> int:
    return int(role.monthly_usd_budget_cents or DEFAULT_USD_BUDGET_MONTHLY_CENTS)


def month_to_date_spend_cents(db: Session, *, role: Role) -> int:
    """Month-to-date charged spend on this role, in cents.

    Aggregates ``UsageEvent.credits_charged`` (raw Anthropic cost ×
    per-feature markup) across every feature where ``role_id`` matches.
    Same unit as ``Role.monthly_usd_budget_cents`` so the cap check and
    every customer-facing display reconcile.
    """
    month_start = _month_start_utc(datetime.now(timezone.utc))
    spent_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.credits_charged), 0))
        .filter(
            UsageEvent.organization_id == role.organization_id,
            UsageEvent.role_id == role.id,
            UsageEvent.created_at >= month_start,
        )
        .scalar()
        or 0
    )
    return int(int(spent_micro) / 10_000)  # micro-USD → cents


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
