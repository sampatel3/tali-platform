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
from typing import Any, Optional

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
# Internal-aggregate + drift math lives in anthropic_reconciliation_aggregates
# now (kept this module under the 500-LOC architecture gate). Re-exported here
# so existing import sites — ``from .anthropic_reconciliation_service import
# _aggregate_internal`` / ``_aggregate_internal_multi`` / ``_model_match_filter``
# — keep working unchanged.
from .anthropic_reconciliation_aggregates import (  # noqa: F401
    _aggregate_internal,
    _aggregate_internal_multi,
    _base_alias_for,
    _model_match_filter,
    _percent_drift,
    _zero_internal,
)

logger = logging.getLogger("taali.anthropic_reconciliation")


# Window we re-pull on every run. The binding lateness is NOT Anthropic's
# (its usage/cost data settles in ~5 min) — it's OUR OWN internal rows: the
# Message Batches retrieve path (cv_matching/runner_batch) lands claude_call_log
# / usage_events rows hours-to-days after the batch was billed, so a day
# reconciled at 03:00 can still be missing batch spend that arrives later.
# Measured 2026-05-30: recomputing 05-26/05-27 against current data raised the
# internal total well above what the 03:00 run stored (drift -21% -> single
# digits). A 4-day daily window re-reconciles each day until its late batch
# rows have settled; the weekly settle sweep (days=14) catches stragglers.
_RECONCILE_LOOKBACK_DAYS = 4

# Drift alerting. A reconciliation that silently records -77% drift for days
# (as happened with the pre-2026-05-26 Graphiti leak) is worse than useless —
# the whole point is to catch un-metered spend loudly. After each run we alert
# on any (day, model) row whose |cost drift| crosses the threshold on a
# MATERIAL day. Tiny days are excluded because a couple of dollars of rounding
# reads as a huge percentage (e.g. a $0.08 day showed -25%). NEGATIVE drift
# (internal < Anthropic billed) is the dangerous direction — we were billed for
# spend we never metered — but we alert on large drift either way.
_ALERT_DRIFT_PCT = 10  # percent
_ALERT_MIN_COST_USD_MICRO = 1_000_000  # $1 — ignore sub-dollar noise rows


def _is_alertable_drift(cost_drift, anthropic_cost_micro: int) -> bool:
    """True when a (day, model) row's cost drift warrants an alert: a real
    drift magnitude on a material-spend day. ``cost_drift`` is the Decimal
    percent from ``_percent_drift`` (None when external spend is 0)."""
    return (
        cost_drift is not None
        and abs(cost_drift) >= _ALERT_DRIFT_PCT
        and anthropic_cost_micro >= _ALERT_MIN_COST_USD_MICRO
    )


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

    # Anthropic's admin API attributes calls under the shared (non-
    # workspace-scoped) API key to ``workspace_id=None`` (the "Default"
    # workspace in the console). Without an explicit mapping, those
    # rows would have ``org_id=None`` below and the internal aggregate
    # would be forced to zero — masking the entire shared-key spend as
    # a -100% drift. Collect every Tali org that *doesn't* have its own
    # workspace key provisioned: those orgs use the shared key, so
    # their UsageEvent rows are the right aggregate to compare against
    # Anthropic's Default-workspace totals.
    _shared_key_org_ids: list[int] = [
        int(o.id)
        for o in db.query(Organization)
        .filter(Organization.anthropic_workspace_id.is_(None))
        .all()
    ]

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

    # De-dup base-alias attribution. Internal rows often store the short
    # alias (``claude-sonnet-4-5``) rather than the dated snapshot. When two
    # dated snapshots share one base alias on the same (day, workspace), the
    # permissive ``_model_match_filter`` would let BOTH Anthropic buckets sum
    # the same alias-stored internal rows → double-count. Designate exactly
    # one dated bucket per (day, workspace, base_alias) to claim the alias
    # rows; the rest match only their exact dated id.
    alias_owner: dict[tuple[date, Optional[str], str], tuple[date, Optional[str], Optional[str]]] = {}
    for (usage_day, workspace_id, model) in by_key:
        base_alias = _base_alias_for(model)
        if base_alias is None:
            continue
        alias_key = (usage_day, workspace_id, base_alias)
        owner = alias_owner.get(alias_key)
        # Deterministic owner: the lexicographically smallest dated id so the
        # choice is stable across runs (upsert idempotency).
        if owner is None or str(model) < str(owner[2]):
            alias_owner[alias_key] = (usage_day, workspace_id, model)

    rows_written = 0
    rows_skipped = 0
    drift_alerts: list[dict[str, Any]] = []
    for (usage_day, workspace_id, model), tokens in by_key.items():
        org_id = workspace_to_org.get(workspace_id) if workspace_id else None
        anthropic_cost = cost_by_key.get((usage_day, workspace_id, model), 0)

        # This bucket claims base-alias internal rows only if it's the
        # designated owner for its (day, workspace, base_alias).
        base_alias = _base_alias_for(model)
        include_alias = (
            base_alias is None
            or alias_owner.get((usage_day, workspace_id, base_alias))
            == (usage_day, workspace_id, model)
        )

        # Pull the matching internal aggregate.
        # - Workspace-scoped key (org_id resolved): aggregate that one org.
        # - Default workspace (workspace_id is None): aggregate across every
        #   org that uses the shared key — that's what produced the
        #   Anthropic-side total. Previously this branch returned zero, so
        #   100% of shared-key spend showed as -100% drift even when the
        #   meter was working correctly.
        # - Unrecognised workspace_id (provisioned outside Tali, never
        #   linked): still zero, surfacing it as drift so we notice the
        #   orphan workspace.
        if org_id is not None:
            internal = _aggregate_internal(
                db,
                organization_id=org_id,
                model=model,
                usage_day=usage_day,
                include_alias=include_alias,
            )
        elif workspace_id is None and _shared_key_org_ids:
            internal = _aggregate_internal_multi(
                db,
                organization_ids=_shared_key_org_ids,
                model=model,
                usage_day=usage_day,
                include_alias=include_alias,
            )
        else:
            internal = _zero_internal()

        tokens_drift = _percent_drift(
            internal=internal["input"] + internal["output"],
            external=tokens["input"] + tokens["output"],
        )
        cost_drift = _percent_drift(
            internal=internal["cost_usd_micro"],
            external=anthropic_cost,
        )

        # Collect a drift alert for material rows that breach the threshold.
        if _is_alertable_drift(cost_drift, anthropic_cost):
            drift_alerts.append({
                "usage_date": usage_day.isoformat(),
                "model": model or "",
                "workspace_id": workspace_id,
                "cost_drift_pct": float(cost_drift),
                "anthropic_cost_usd": round(anthropic_cost / 1e6, 2),
                "internal_cost_usd": round(internal["cost_usd_micro"] / 1e6, 2),
                "direction": "under_count" if cost_drift < 0 else "over_count",
            })

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

    if drift_alerts:
        # Most-negative (worst under-count) first.
        drift_alerts.sort(key=lambda a: a["cost_drift_pct"])
        worst = drift_alerts[:5]
        logger.error(
            "anthropic_reconciliation_drift_alert: %d (day,model) row(s) exceed "
            "%d%% cost drift on material spend. Worst: %s",
            len(drift_alerts), _ALERT_DRIFT_PCT, worst,
            extra={"event": "anthropic_reconciliation_drift_alert", "alerts": worst},
        )
        try:  # surface to Sentry if configured (main.py inits it)
            import sentry_sdk

            w = worst[0]
            sentry_sdk.capture_message(
                f"Anthropic reconciliation drift: {len(drift_alerts)} row(s) over "
                f"{_ALERT_DRIFT_PCT}% (worst {w['cost_drift_pct']:.1f}% {w['direction']} "
                f"on {w['model']} {w['usage_date']})",
                level="error",
            )
        except Exception:  # pragma: no cover — never let alerting break the run
            pass

    return {
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
        "window_start": starting_at.isoformat(),
        "window_end": ending_at.isoformat(),
        "drift_alerts": len(drift_alerts),
        "drift_alert_details": drift_alerts[:10],
    }
