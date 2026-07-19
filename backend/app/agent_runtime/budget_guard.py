"""Per-job budget enforcement.

One layer left, after a long compaction:

**Monthly USD cap** (universal). Sum ``UsageEvent.credits_charged`` across
*all* features (scoring, pre-screen, assessment, agent) for the role in
the current calendar month and refuse if over
``role.monthly_usd_budget_cents``. Pauses the role on hit and is
checked from every Anthropic-spending entry point that has role context
— when agentic mode is on, this budget covers everything the platform
does for that role, not just the agent. Runtime safety can also pause a role
when platform credits are depleted or an activation bootstrap exhausts its
retries; every automatic hold requires its underlying condition to be healthy
before a resume can clear it.

Per-cycle limits are intentionally enforced at the boundaries that own them:
``orchestrator.run_cycle`` enforces ``agent_token_budget_per_cycle`` before and
after each paid call, while ``tool_registry.dispatch`` enforces
``agent_decision_budget_per_cycle`` plus the candidate-facing action caps. They
abort one cycle but never permanently pause a role. The monthly cap below is
the recurring spend guard; bootstrap and credit safety holds are enforced at
their respective task boundaries.

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
from ..services.agent_pause_reasons import (
    MANUAL_PAUSE_REASON,
    WORKSPACE_BULK_PAUSE_REASON,
)


DEFAULT_USD_BUDGET_MONTHLY_CENTS = 5_000  # $50.00


def is_manual_pause_reason(reason: str | None) -> bool:
    """Recognize canonical and legacy recruiter-authored pause labels.

    Older rows/UI versions used "paused by you" or "manual pause". Treating
    those as system holds would let the recovery sweep undo an explicit human
    stop, so automatic callers use this conservative classifier.
    """
    normalized = " ".join(str(reason or "").strip().lower().split())
    return normalized in {
        MANUAL_PAUSE_REASON,
        "paused by you",
        "manual pause",
        "manually paused by recruiter",
        WORKSPACE_BULK_PAUSE_REASON,
    }


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


def micro_to_cents(micro) -> int:
    """The one micro-credits → cents conversion (1 cent = 10_000 micro-credits).
    Every budget/usage cents figure goes through here so units never diverge."""
    return int(int(micro or 0) / 10_000)


def role_monthly_usd_cents(role: Role) -> int:
    raw_cap = getattr(role, "monthly_usd_budget_cents", None)
    if raw_cap is None:
        return DEFAULT_USD_BUDGET_MONTHLY_CENTS
    parsed_cap = int(raw_cap)
    return parsed_cap if parsed_cap > 0 else DEFAULT_USD_BUDGET_MONTHLY_CENTS


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
    return micro_to_cents(month_to_date_spend_microcredits(db, role=role))


def month_to_date_spend_microcredits(db: Session, *, role: Role) -> int:
    """Exact month-to-date charged spend for admission math.

    Budget displays use whole cents, but projected job admission must retain
    micro-credit precision or a near-cap role can squeeze in one extra call via
    cents truncation.
    """
    return int(
        db.query(func.coalesce(func.sum(UsageEvent.credits_charged), 0))
        .filter(
            UsageEvent.organization_id == role.organization_id,
            UsageEvent.role_id == role.id,
            UsageEvent.created_at >= month_start(),
        )
        .scalar()
        or 0
    )


def active_score_commitment_count(db: Session, *, role: Role) -> int:
    """Pending/running score jobs whose usage has not necessarily landed yet."""
    from ..models.cv_score_job import (
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
        CvScoreJob,
    )

    return int(
        db.query(func.count(CvScoreJob.id))
        .filter(
            CvScoreJob.role_id == int(role.id),
            CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
        )
        .scalar()
        or 0
    )


def remaining_role_admission_microcredits(
    db: Session,
    *,
    role: Role,
    per_active_score_job: int,
) -> int | None:
    """Remaining cap after actual spend and projected in-flight score jobs.

    ``None`` means the role has no configured monthly cap. A legacy zero value
    uses the documented $50 fallback; it must never become an unlimited hard-
    admission path while the cycle-level check sees a finite cap. This is a
    bounded admission reservation rather than a durable monetary hold: active
    ``CvScoreJob`` rows are the existing durable evidence of committed work,
    and each is conservatively valued at the caller's SCORE reservation.
    """
    raw_cap = getattr(role, "monthly_usd_budget_cents", None)
    if raw_cap is None:
        return None
    effective_cap = (
        int(raw_cap)
        if int(raw_cap) > 0
        else DEFAULT_USD_BUDGET_MONTHLY_CENTS
    )
    cap_micro = effective_cap * 10_000
    spent_micro = month_to_date_spend_microcredits(db, role=role)
    active_commitment = active_score_commitment_count(db, role=role) * max(
        int(per_active_score_job), 0
    )
    return max(cap_micro - spent_micro - active_commitment, 0)


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


def resume_if_under_budget(
    db: Session,
    *,
    role: Role,
    explicit: bool = False,
) -> bool:
    """Clear an agent hold once budget and runtime readiness are healthy.

    The inverse of :func:`pause_role`. A role can be held by its monthly cap,
    depleted platform credits, a failed bootstrap, or an explicit soft pause.
    In every case this helper clears the hold only after both the monthly cap
    and the complete production-readiness probe are green. The cohort sweep
    skips paused roles (``agent_paused_at IS NULL``), so the caller must also
    queue an immediate cycle after a successful resume.

    Acts only when the role is still agent-enabled (a manually disabled
    agent stays off), currently paused, *now genuinely under the cap*, and its
    production runtime is ready. Automatic callers (for example, a budget
    field edit) use the fail-safe default ``explicit=False``; that mode never
    clears a recruiter-authored manual pause. Explicit Resume/Turn-on surfaces
    must opt in with ``explicit=True``. Keeping the readiness check at this
    shared mutation boundary makes every resume surface fail closed:
    direct/resume-all routes, budget-cap PATCHes, chat settings, and
    recruiter-answer writeback.
    Returns ``True`` when a pause was actually cleared, so the caller can kick
    an immediate cycle instead of waiting for the beat.
    """
    if role.agent_paused_at is None or not bool(role.agentic_mode_enabled):
        return False
    if not explicit and is_manual_pause_reason(role.agent_paused_reason):
        return False
    if not check_monthly_usd(db, role=role).ok:
        return False
    try:
        from ..services.agent_activation_readiness import activation_readiness

        readiness = activation_readiness(role)
    except Exception:
        # Resume is a state-changing promise that work can run. An unexpected
        # readiness-probe failure must leave the role paused, not optimistically
        # clear the only guard that stops the cohort sweep.
        import logging

        logging.getLogger("taali.agent.budget_guard").exception(
            "agent resume readiness probe failed role_id=%s", role.id
        )
        return False
    if not readiness.get("ready"):
        import logging

        logging.getLogger("taali.agent.budget_guard").warning(
            "agent resume withheld: runtime unready role_id=%s reasons=%s",
            role.id,
            readiness.get("reasons"),
        )
        return False
    now = datetime.now(timezone.utc)
    role.agent_paused_at = None
    role.agent_paused_reason = None
    role.agent_bootstrap_status = "starting"
    role.agent_bootstrap_error = None
    role.agent_bootstrap_started_at = now
    role.agent_bootstrap_completed_at = None
    db.add(role)
    return True
