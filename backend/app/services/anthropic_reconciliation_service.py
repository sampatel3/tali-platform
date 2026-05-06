"""Reconcile internal ``usage_events`` against Anthropic billing data.

Pulls authoritative per-day, per-workspace, per-model usage + cost from
Anthropic's Admin API and writes one row per (date, workspace, model)
to ``anthropic_usage_reconciliations``. The settings → usage tab surfaces
drift > 1% so spend that bypasses the meter is caught quickly.

Run cadence: daily Celery beat at 03:00 UTC reconciles the prior 48
hours. The 48h window means data settles (Anthropic's ~5min freshness
lag means a same-day pull would under-report the last few minutes) and
the second day's pull naturally upserts the previous day's row if any
late-arriving usage shifts numbers.

The service has no rate-limit logic — daily volume is small (~1
request per day per workspace; pagination handled inside the report
client). If we ever start hourly recon, add backoff there first.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..components.integrations.anthropic_admin.usage_reports import (
    AnthropicUsageError,
    UsageBucket,
    fetch_cost_buckets,
    fetch_usage_buckets,
    is_configured as admin_is_configured,
)
from ..models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from ..models.organization import Organization
from ..models.usage_event import UsageEvent

logger = logging.getLogger("taali.anthropic_reconciliation")


# Window we re-pull on every run. 48h gives Anthropic late data time to
# settle without making the daily query expensive.
_RECONCILE_LOOKBACK_DAYS = 2


def reconcile_recent(
    db: Session,
    *,
    days: int = _RECONCILE_LOOKBACK_DAYS,
    end_date: Optional[date] = None,
) -> dict:
    """Reconcile the last ``days`` days. Idempotent — safe to run more
    than once per day; rows are upserted by (date, workspace, model).

    Returns a dict summary of rows written and any errors encountered.
    Caller (Celery task) logs the result.
    """
    if not admin_is_configured():
        return {
            "skipped": True,
            "reason": "ANTHROPIC_ADMIN_API_KEY not configured",
        }

    end = end_date or datetime.now(timezone.utc).date()
    # We pull whole UTC days. ``starting_at`` is the 00:00 UTC start of
    # the earliest day; ``ending_at`` is exclusive, so 00:00 UTC of the
    # day AFTER the last day we want. Anthropic snaps to bucket
    # boundaries automatically.
    starting_at = datetime.combine(
        end - timedelta(days=days), time(0, 0), tzinfo=timezone.utc
    )
    ending_at = datetime.combine(
        end + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
    )

    # Fetch + flatten Anthropic's two reports. Both endpoints stream
    # buckets via httpx; we materialize because the per-day volume is
    # small (a few hundred rows worst case).
    try:
        usage_rows = list(
            fetch_usage_buckets(
                starting_at=starting_at,
                ending_at=ending_at,
                bucket_width="1d",
            )
        )
    except AnthropicUsageError as exc:
        logger.warning("Anthropic usage report fetch failed: %s", exc)
        return {"error": f"usage_fetch_failed: {exc}"}

    try:
        cost_rows = list(
            fetch_cost_buckets(
                starting_at=starting_at,
                ending_at=ending_at,
            )
        )
    except AnthropicUsageError as exc:
        # Costs are nice-to-have — without them the reconciliation row
        # still tracks token drift. Log + continue with empty cost map.
        logger.warning("Anthropic cost report fetch failed: %s", exc)
        cost_rows = []

    # Sum costs by (date, workspace_id, model) so we can attach a
    # cost number to each (date, workspace, model) usage bucket. The
    # cost endpoint groups by description (token_type + model) — we
    # collapse by model.
    cost_by_key: dict[tuple[date, Optional[str], Optional[str]], int] = defaultdict(int)
    for cb in cost_rows:
        if cb.cost_type and cb.cost_type != "tokens":
            # Skip web_search / code_execution / session_usage for the
            # per-model token reconciliation — those are tracked in the
            # ``details`` blob. The internal meter doesn't bill for them
            # yet so including them would create false-positive drift.
            continue
        usage_day = cb.starting_at.date()
        # cost report's micro-USD: 1 cent = 10_000 micro
        micro_usd = cb.amount_cents * 10_000
        cost_by_key[(usage_day, cb.workspace_id, cb.model)] += micro_usd

    # Map Anthropic workspace_id → Tali organization_id once for the run.
    workspace_to_org = {
        org.anthropic_workspace_id: int(org.id)
        for org in db.query(Organization)
        .filter(Organization.anthropic_workspace_id.isnot(None))
        .all()
    }

    # Aggregate Anthropic rows per (date, workspace, model). The Admin
    # API already returns rows at this granularity (we group_by
    # workspace + model), but service_tier / context_window can split
    # the same key into multiple rows we want collapsed.
    by_key: dict[tuple[date, Optional[str], Optional[str]], dict[str, int]] = defaultdict(
        lambda: {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_creation": 0,
        }
    )
    for ub in usage_rows:
        usage_day = ub.starting_at.date()
        key = (usage_day, ub.workspace_id, ub.model)
        agg = by_key[key]
        agg["input"] += ub.uncached_input_tokens
        agg["output"] += ub.output_tokens
        agg["cache_read"] += ub.cache_read_input_tokens
        agg["cache_creation"] += ub.cache_creation_input_tokens

    rows_written = 0
    rows_skipped = 0
    for (usage_day, workspace_id, model), tokens in by_key.items():
        org_id = workspace_to_org.get(workspace_id) if workspace_id else None
        anthropic_cost = cost_by_key.get((usage_day, workspace_id, model), 0)

        # Pull the matching internal aggregate. Match by org_id when we
        # can resolve the workspace; otherwise leave internal at 0
        # (drift will be 100% under-counted, surfacing the unrecognised
        # workspace in the UI).
        internal = (
            _aggregate_internal(
                db,
                organization_id=org_id,
                model=model,
                usage_day=usage_day,
            )
            if org_id is not None
            else _zero_internal()
        )

        tokens_drift = _percent_drift(
            internal=internal["input"] + internal["output"],
            external=tokens["input"] + tokens["output"],
        )
        cost_drift = _percent_drift(
            internal=internal["cost_usd_micro"],
            external=anthropic_cost,
        )

        details: dict[str, Any] = {
            "anthropic_workspace_known": workspace_id in workspace_to_org,
            "internal_event_count": internal["event_count"],
        }

        existing = (
            db.query(AnthropicUsageReconciliation)
            .filter(
                AnthropicUsageReconciliation.usage_date == usage_day,
                AnthropicUsageReconciliation.anthropic_workspace_id.is_(workspace_id)
                if workspace_id is None
                else AnthropicUsageReconciliation.anthropic_workspace_id == workspace_id,
                AnthropicUsageReconciliation.model == (model or ""),
            )
            .one_or_none()
        )
        if existing is None:
            row = AnthropicUsageReconciliation(
                usage_date=usage_day,
                anthropic_workspace_id=workspace_id,
                organization_id=org_id,
                model=model or "",
                anthropic_input_tokens=tokens["input"],
                anthropic_output_tokens=tokens["output"],
                anthropic_cache_read_tokens=tokens["cache_read"],
                anthropic_cache_creation_tokens=tokens["cache_creation"],
                anthropic_cost_usd_micro=anthropic_cost,
                internal_input_tokens=internal["input"],
                internal_output_tokens=internal["output"],
                internal_cache_read_tokens=internal["cache_read"],
                internal_cache_creation_tokens=internal["cache_creation"],
                internal_cost_usd_micro=internal["cost_usd_micro"],
                internal_event_count=internal["event_count"],
                tokens_drift_pct=tokens_drift,
                cost_drift_pct=cost_drift,
                details=details,
            )
            db.add(row)
        else:
            existing.organization_id = org_id
            existing.anthropic_input_tokens = tokens["input"]
            existing.anthropic_output_tokens = tokens["output"]
            existing.anthropic_cache_read_tokens = tokens["cache_read"]
            existing.anthropic_cache_creation_tokens = tokens["cache_creation"]
            existing.anthropic_cost_usd_micro = anthropic_cost
            existing.internal_input_tokens = internal["input"]
            existing.internal_output_tokens = internal["output"]
            existing.internal_cache_read_tokens = internal["cache_read"]
            existing.internal_cache_creation_tokens = internal["cache_creation"]
            existing.internal_cost_usd_micro = internal["cost_usd_micro"]
            existing.internal_event_count = internal["event_count"]
            existing.tokens_drift_pct = tokens_drift
            existing.cost_drift_pct = cost_drift
            existing.reconciled_at = datetime.now(timezone.utc)
            existing.details = details
        rows_written += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to commit reconciliation rows")
        return {"error": "commit_failed", "rows_attempted": rows_written}

    return {
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
        "window_start": starting_at.isoformat(),
        "window_end": ending_at.isoformat(),
    }


def _zero_internal() -> dict[str, int]:
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "cost_usd_micro": 0,
        "event_count": 0,
    }


def _aggregate_internal(
    db: Session,
    *,
    organization_id: int,
    model: Optional[str],
    usage_day: date,
) -> dict[str, int]:
    """Sum ``usage_events`` for one org / model / UTC day.

    The model filter is permissive — Anthropic reports use the
    canonical model id (e.g. ``claude-haiku-4-5-20251001``), and our
    internal events store the same string passed to
    ``messages.create(model=...)``. If a caller used an alias, the
    internal aggregate may legitimately split across rows. Surface as
    drift so it's investigated.
    """
    day_start = datetime.combine(usage_day, time(0, 0), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    q = db.query(
        func.count(UsageEvent.id).label("event_count"),
        func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(
            func.sum(UsageEvent.cache_read_tokens), 0
        ).label("cache_read_tokens"),
        func.coalesce(
            func.sum(UsageEvent.cache_creation_tokens), 0
        ).label("cache_creation_tokens"),
        func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0).label("cost_usd_micro"),
    ).filter(
        UsageEvent.organization_id == organization_id,
        UsageEvent.created_at >= day_start,
        UsageEvent.created_at < day_end,
    )
    if model:
        q = q.filter(UsageEvent.model == model)
    row = q.one()
    return {
        "input": int(row.input_tokens or 0),
        "output": int(row.output_tokens or 0),
        "cache_read": int(row.cache_read_tokens or 0),
        "cache_creation": int(row.cache_creation_tokens or 0),
        "cost_usd_micro": int(row.cost_usd_micro or 0),
        "event_count": int(row.event_count or 0),
    }


def _percent_drift(*, internal: int, external: int) -> Optional[Decimal]:
    """Return ``(internal - external) / external * 100`` as a Decimal,
    quantized to 0.001%. Negative = internal under-counted.

    Returns ``None`` when external is 0 (drift undefined). Positive
    drift over 0% with external==0 means we recorded spend Anthropic
    didn't bill for — also surfaced as None so the UI shows it
    distinctly (orphan internal rows).
    """
    if external == 0:
        if internal == 0:
            return Decimal("0.000")
        return None
    diff = Decimal(internal - external) / Decimal(external) * Decimal(100)
    return diff.quantize(Decimal("0.001"))
