"""Per-job budget enforcement.

Two layers:

1. Pre-call gate (agent loop only): before each Anthropic round, refuse
   if the running cycle has already exceeded
   ``role.agent_token_budget_per_cycle`` or
   ``role.agent_decision_budget_per_cycle``. These are runaway-loop
   guards, not money — they bound a single cycle's behaviour.
2. Monthly USD cap (universal): sum ``UsageEvent.cost_usd_micro`` across
   *all* features (scoring, pre-screen, assessment, agent) for the role
   in the current calendar month and refuse if over
   ``role.monthly_usd_budget_cents``. Pauses the role on hit and is
   checked from every Anthropic-spending entry point that has role
   context — so when agentic mode is on, the budget covers everything
   the platform does for that role, not just the agent.

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


DEFAULT_TOKEN_BUDGET_PER_CYCLE = 50_000
DEFAULT_DECISION_BUDGET_PER_CYCLE = 20
DEFAULT_USD_BUDGET_MONTHLY_CENTS = 5_000  # $50.00


@dataclass(frozen=True)
class BudgetCheck:
    ok: bool
    reason: Optional[str] = None


def _month_start_utc(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def role_token_budget(role: Role) -> int:
    return int(role.agent_token_budget_per_cycle or DEFAULT_TOKEN_BUDGET_PER_CYCLE)


def role_decision_budget(role: Role) -> int:
    return int(role.agent_decision_budget_per_cycle or DEFAULT_DECISION_BUDGET_PER_CYCLE)


def role_monthly_usd_cents(role: Role) -> int:
    return int(role.monthly_usd_budget_cents or DEFAULT_USD_BUDGET_MONTHLY_CENTS)


def check_pre_round(*, role: Role, tokens_used: int, decisions_emitted: int) -> BudgetCheck:
    if role.agent_paused_at is not None:
        return BudgetCheck(ok=False, reason=f"role paused: {role.agent_paused_reason or 'unspecified'}")
    if tokens_used >= role_token_budget(role):
        return BudgetCheck(ok=False, reason=f"per-cycle token budget exhausted ({tokens_used})")
    if decisions_emitted >= role_decision_budget(role):
        return BudgetCheck(ok=False, reason=f"per-cycle decision budget exhausted ({decisions_emitted})")
    return BudgetCheck(ok=True)


def month_to_date_spend_cents(db: Session, *, role: Role) -> int:
    """Sum *all* Anthropic spend on this role for the current month, in cents.

    Aggregates ``UsageEvent.cost_usd_micro`` across every feature
    (scoring, pre-screen, assessment, agent) where ``role_id`` matches.
    """
    month_start = _month_start_utc(datetime.now(timezone.utc))
    spent_micro = (
        db.query(func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0))
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
