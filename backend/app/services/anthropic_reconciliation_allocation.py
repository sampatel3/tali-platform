"""Per-org allocation of the shared-key Anthropic cost.

100% of prod spend is on the shared/default Anthropic key, so Anthropic's Admin
API only reports the AGGREGATE — it cannot attribute cost per org. These helpers
distribute the reconciled Anthropic total across orgs by each org's captured
cost share, so per-org cost ties to the Anthropic total exactly (the per-org
numbers sum to what Anthropic actually billed).

This is an allocation, not a measurement: it inherits the aggregate's accuracy
(currently ~−0.4%). TRUE per-org reconciliation — verifying each org's cost
against Anthropic independently — requires per-org Anthropic WORKSPACE keys
(``Organization.anthropic_workspace_id`` + the ``workspace_to_org`` map in
``reconcile_recent`` already support that path; the missing piece is
provisioning a workspace/key per org and routing each org's calls through it).
That's an infra decision, not buildable here.

Lives in its own module to keep ``anthropic_reconciliation_service`` under the
500-LOC architecture gate.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from ..models.usage_event import UsageEvent


def reconciliation_factor(
    db: Session, *, start_date: date, end_date: date
) -> Optional[float]:
    """``anthropic_total / internal_total`` over [start, end] from the stored
    reconciliation rows. ~1.0 means our capture matches Anthropic; the current
    aggregate sits at ~0.996. Returns None when there is no internal spend."""
    row = (
        db.query(
            func.coalesce(
                func.sum(AnthropicUsageReconciliation.anthropic_cost_usd_micro), 0
            ),
            func.coalesce(
                func.sum(AnthropicUsageReconciliation.internal_cost_usd_micro), 0
            ),
        )
        .filter(
            AnthropicUsageReconciliation.usage_date >= start_date,
            AnthropicUsageReconciliation.usage_date <= end_date,
        )
        .one()
    )
    anthropic_micro, internal_micro = int(row[0] or 0), int(row[1] or 0)
    if internal_micro <= 0:
        return None
    return anthropic_micro / internal_micro


def allocate_reconciled_cost_by_org(
    db: Session, *, start_date: date, end_date: date
) -> dict[int, int]:
    """Allocate the reconciled Anthropic cost across orgs by each org's captured
    (non-cache) cost share. Returns ``{organization_id: allocated_cost_micro}``.

    The allocated totals sum to the reconciled Anthropic total for the window,
    so per-org cost is tied to what Anthropic actually billed (within the
    aggregate's ~0.4% accuracy) despite the shared key hiding per-org detail.
    Cache hits are excluded (no Anthropic call ⇒ $0), matching the raw-cost
    convention in budget_guard / usage_summary.
    """
    factor = reconciliation_factor(db, start_date=start_date, end_date=end_date)
    if factor is None:
        return {}
    window_start = datetime.combine(start_date, time(0, 0), tzinfo=timezone.utc)
    window_end = datetime.combine(
        end_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
    )
    rows = (
        db.query(
            UsageEvent.organization_id,
            func.coalesce(
                func.sum(
                    case(
                        (UsageEvent.cache_hit == 0, UsageEvent.cost_usd_micro),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .filter(
            UsageEvent.created_at >= window_start,
            UsageEvent.created_at < window_end,
            UsageEvent.organization_id.isnot(None),
        )
        .group_by(UsageEvent.organization_id)
        .all()
    )
    return {
        int(org_id): int(round(int(captured) * factor))
        for org_id, captured in rows
        if captured and int(captured) > 0
    }
