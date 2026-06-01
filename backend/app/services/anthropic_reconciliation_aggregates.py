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


def _sum_table(db, table, *, organization_ids, model, day_start, day_end, include_alias=True, extra_filters=None, include_null_org=False) -> dict[str, int]:
    """Sum the token/cost columns of either ``ClaudeCallLog`` or
    ``UsageEvent`` (same column names) for the org set / model / day.

    The model filter is permissive — Anthropic reports use the dated
    snapshot id; internal rows sometimes store the short alias.
    ``_model_match_filter`` accepts both. ``extra_filters`` appends extra
    SQL predicates (e.g. excluding usage_events already linked to a
    call_log row).

    ``include_null_org``: also count rows whose ``organization_id`` is NULL.
    An ``IN (...)`` predicate never matches NULL, so without this every
    unattributed call (notably Graphiti graph_sync, whose async wrapper
    writes ``claude_call_log`` with ``organization_id=None`` when the
    metering contextvar isn't set) is silently dropped from the internal
    aggregate. For the shared-key (Default-workspace) bucket those NULL-org
    calls ARE shared-key spend Anthropic billed, so they must be counted —
    dropping them was the dominant cause of the −28%..−46% Haiku drift.
    """
    org_predicate = table.organization_id.in_(organization_ids)
    if include_null_org:
        org_predicate = func.coalesce(table.organization_id.in_(organization_ids), False) | (
            table.organization_id.is_(None)
        )
    q = db.query(
        func.count(table.id).label("event_count"),
        func.coalesce(func.sum(table.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(table.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(table.cache_read_tokens), 0).label("cache_read_tokens"),
        func.coalesce(func.sum(table.cache_creation_tokens), 0).label("cache_creation_tokens"),
        func.coalesce(func.sum(table.cost_usd_micro), 0).label("cost_usd_micro"),
    ).filter(
        org_predicate,
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
    include_null_org: bool = False,
) -> dict[str, int]:
    """Internal aggregate across multiple orgs for one (model, day).

    Used both for the single-org case and for the shared-key
    (workspace_id=None) case where Anthropic's Default-workspace bucket
    is the sum of every Tali org on the shared key.

    ``include_null_org`` adds rows with ``organization_id IS NULL`` to the
    aggregate. Set ONLY for the shared-key bucket: unattributed calls
    (Graphiti graph_sync writes ``claude_call_log`` with NULL org when the
    metering contextvar is unset) run on the shared key and are billed by
    Anthropic to the Default workspace, so they belong in this bucket. Left
    False for workspace-scoped orgs so a NULL-org call isn't double-counted
    into every org's bucket.

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
        include_null_org=include_null_org,
    )
    if call_log["event_count"] == 0:
        # Pre-#237 day (no call_log at all): the legacy usage_events
        # aggregate is the only internal number available. NULL-org
        # usage_events don't exist (record_event requires an org), so
        # include_null_org is a no-op on this fallback.
        return _sum_table(
            db, UsageEvent,
            organization_ids=organization_ids, model=model,
            day_start=day_start, day_end=day_end,
            include_alias=include_alias,
            include_null_org=include_null_org,
        )

    # call_log present: add usage_events that have no linked call_log row in
    # this (org-set, day) so usage-events-only calls aren't dropped, without
    # double-counting calls that wrote both.
    #
    # Two categories of unlinked usage_events MUST be excluded — they're
    # not Anthropic spend, and the original (#251) union counted them
    # anyway, producing reconciliation drift up to +138% on Haiku
    # (2026-05-25, caught 2026-05-26):
    #
    # 1. ``cache_hit=1`` — cv_score_orchestrator / pre_screening_service
    #    write a usage_event on a cache HIT (no Anthropic call made; we
    #    just charge the customer for the cached result). Including it
    #    inflates "internal Anthropic cost" with money Anthropic never
    #    billed us for.
    #
    # 2. ``feature='agent_autonomous'`` — the agent orchestrator passes
    #    ``metering={"skip": True}`` to the wrapper (so the wrapper writes
    #    only the call_log row) and then writes its OWN usage_event via
    #    ``record_event`` for richer attribution (role_id, agent_run_id).
    #    Both rows represent the SAME Anthropic call. Counting the
    #    unlinked usage_event on top of the call_log row double-counts
    #    the agent path.
    #
    # Both exclusions are safe: (1) cache hits by definition didn't talk to
    # Anthropic; (2) the agent path is fully represented in claude_call_log
    # via the wrapper's unconditional write since #237.
    _linked_org_predicate = ClaudeCallLog.organization_id.in_(organization_ids)
    if include_null_org:
        _linked_org_predicate = _linked_org_predicate | ClaudeCallLog.organization_id.is_(None)
    linked_usage_event_ids = (
        db.query(ClaudeCallLog.usage_event_id)
        .filter(
            ClaudeCallLog.usage_event_id.isnot(None),
            _linked_org_predicate,
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
        extra_filters=[
            ~UsageEvent.id.in_(linked_usage_event_ids),
            UsageEvent.cache_hit == 0,
            UsageEvent.feature != "agent_autonomous",
        ],
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
    diff = diff.quantize(Decimal("0.001"))
    # The drift columns are Numeric(7,3) -> max ±9999.999. When internal is
    # >100x external (e.g. a near-zero-Anthropic day with real internal spend,
    # or a wide-window re-reconcile of an old day), the raw percentage overflows
    # the column and the whole commit fails. Clamp to the column range — a
    # clamped value still reads as "wildly off" and trips the drift alert, which
    # is all the magnitude needs to convey past this point.
    _CLAMP = Decimal("9999.999")
    if diff > _CLAMP:
        return _CLAMP
    if diff < -_CLAMP:
        return -_CLAMP
    return diff
