"""Internal-aggregate + drift math for Anthropic usage reconciliation.

Extracted from ``anthropic_reconciliation_service`` to keep that module
under the 500-LOC architecture gate. These helpers turn the internal
``claude_call_log`` / ``usage_events`` tables into per (org-set, model,
day) token + cost aggregates and compute the percentage drift against
Anthropic's billed numbers. They are query/transform helpers — no
orchestration, no commits.

``anthropic_reconciliation_service`` imports these back, so existing
import sites (``from .anthropic_reconciliation_service import
_aggregate_internal`` etc.) keep working unchanged.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent


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
    include_alias: bool = True,
) -> dict[str, int]:
    """Internal aggregate for one org / model / UTC day. Delegates to the
    multi-org helper, which prefers ``claude_call_log`` (ground truth)
    over ``usage_events``."""
    return _aggregate_internal_multi(
        db,
        organization_ids=[organization_id],
        model=model,
        usage_day=usage_day,
        include_alias=include_alias,
    )


def _base_alias_for(anthropic_model: Optional[str]) -> Optional[str]:
    """Return the snapshot-stripped base alias for a dated Anthropic id
    (``claude-sonnet-4-5-20250929`` → ``claude-sonnet-4-5``), or None when
    the id carries no ``-YYYYMMDD`` snapshot suffix."""
    if not anthropic_model:
        return None
    if (
        len(anthropic_model) > 9
        and anthropic_model[-9] == "-"
        and anthropic_model[-8:].isdigit()
    ):
        return anthropic_model[:-9]
    return None


def _sum_table(db, table, *, organization_ids, model, day_start, day_end, include_alias=True, extra_filters=None) -> dict[str, int]:
    """Sum the token/cost columns of either ``ClaudeCallLog`` or
    ``UsageEvent`` (same column names) for the org set / model / day.

    The model filter is permissive — Anthropic reports use the dated
    snapshot id; internal rows sometimes store the short alias.
    ``_model_match_filter`` accepts both. ``extra_filters`` appends extra
    SQL predicates (e.g. excluding usage_events already linked to a
    call_log row).
    """
    q = db.query(
        func.count(table.id).label("event_count"),
        func.coalesce(func.sum(table.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(table.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(table.cache_read_tokens), 0).label("cache_read_tokens"),
        func.coalesce(func.sum(table.cache_creation_tokens), 0).label("cache_creation_tokens"),
        func.coalesce(func.sum(table.cost_usd_micro), 0).label("cost_usd_micro"),
    ).filter(
        table.organization_id.in_(organization_ids),
        table.created_at >= day_start,
        table.created_at < day_end,
    )
    if model:
        q = q.filter(_model_match_filter(table.model, model, include_alias=include_alias))
    for f in (extra_filters or []):
        q = q.filter(f)
    row = q.one()
    return {
        "input": int(row.input_tokens or 0),
        "output": int(row.output_tokens or 0),
        "cache_read": int(row.cache_read_tokens or 0),
        "cache_creation": int(row.cache_creation_tokens or 0),
        "cost_usd_micro": int(row.cost_usd_micro or 0),
        "event_count": int(row.event_count or 0),
    }


def _model_match_filter(stored_col, anthropic_model: Optional[str], *, include_alias: bool = True):
    """Build a SQL filter that matches ``stored_col`` against an Anthropic
    model id whether or not the stored event includes the ``-YYYYMMDD``
    snapshot suffix.

    Anthropic's admin API always returns the dated snapshot
    (``claude-sonnet-4-5-20250929``). Internal events sometimes store the
    short alias (``claude-sonnet-4-5``) because callers pass
    ``settings.resolved_claude_model`` which resolves to the alias. The
    exact-match join missed those rows entirely — Sonnet 4.5 reconciled
    to $0 internal on 2026-05-20 even though we recorded $9.08 of it.

    ``include_alias=False`` matches only the exact dated id. The caller sets
    this for every dated bucket except the one designated to own the base
    alias on a given (day, workspace), so alias-stored internal rows are
    counted into exactly one bucket instead of every snapshot that shares
    the alias.
    """
    if not anthropic_model:
        return stored_col == anthropic_model
    candidates = [anthropic_model]
    # If the Anthropic id ends in ``-8-digit-snapshot``, also accept the
    # snapshot-stripped base id (unless this bucket isn't the alias owner).
    base_alias = _base_alias_for(anthropic_model)
    if include_alias and base_alias is not None:
        candidates.append(base_alias)
    return stored_col.in_(candidates)


def _aggregate_internal_multi(
    db: Session,
    *,
    organization_ids: list[int],
    model: Optional[str],
    usage_day: date,
    include_alias: bool = True,
) -> dict[str, int]:
    """Internal aggregate across multiple orgs for one (model, day).

    Used both for the single-org case and for the shared-key
    (workspace_id=None) case where Anthropic's Default-workspace bucket
    is the sum of every Tali org on the shared key.

    **Source of truth = ``claude_call_log``.** Every Anthropic call
    writes a call_log row unconditionally (since PR #237), so it captures
    spend that ``usage_events`` missed — the whole reason the 2026-05-20
    reconciliation showed a 73% gap. We prefer it.

    **Fallback = ``usage_events``** for any (org-set, day) where the
    call_log has zero rows — i.e. dates before #237 deployed. Self-
    healing: no hardcoded cutover date.

    **Partial-coverage days** (some calls wrote a call_log row, others only
    a ``usage_event`` — e.g. paths that meter via ``record_event`` without
    going through the metered client) used to drop the usage-events-only
    spend entirely. We instead add the ``usage_events`` that are NOT linked
    to a call_log row (``claude_call_log.usage_event_id``) on top of the
    call_log totals: linked events are already represented by their call_log
    row (no double-count), unlinked events are real spend call_log missed.
    """
    if not organization_ids:
        return _zero_internal()
    day_start = datetime.combine(usage_day, time(0, 0), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    call_log = _sum_table(
        db, ClaudeCallLog,
        organization_ids=organization_ids, model=model,
        day_start=day_start, day_end=day_end,
        include_alias=include_alias,
    )
    if call_log["event_count"] == 0:
        # Pre-#237 day (no call_log at all): the legacy usage_events
        # aggregate is the only internal number available.
        return _sum_table(
            db, UsageEvent,
            organization_ids=organization_ids, model=model,
            day_start=day_start, day_end=day_end,
            include_alias=include_alias,
        )

    # call_log present: add usage_events that have no linked call_log row in
    # this (org-set, day) so usage-events-only calls aren't dropped, without
    # double-counting calls that wrote both.
    linked_usage_event_ids = (
        db.query(ClaudeCallLog.usage_event_id)
        .filter(
            ClaudeCallLog.usage_event_id.isnot(None),
            ClaudeCallLog.organization_id.in_(organization_ids),
            ClaudeCallLog.created_at >= day_start,
            ClaudeCallLog.created_at < day_end,
        )
        .scalar_subquery()
    )
    unlinked_usage = _sum_table(
        db, UsageEvent,
        organization_ids=organization_ids, model=model,
        day_start=day_start, day_end=day_end,
        include_alias=include_alias,
        extra_filters=[~UsageEvent.id.in_(linked_usage_event_ids)],
    )
    return {k: call_log[k] + unlinked_usage[k] for k in call_log}


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
