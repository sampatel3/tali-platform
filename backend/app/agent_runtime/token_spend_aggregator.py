"""Per-decision token-spend roll-up — discipline §8.5.

Aggregates the ``usage_events`` rows for a given ``agent_run_id`` into
the compact JSON shape stored on ``AgentDecision.token_spend``.

The per-call data already lives in ``usage_events`` (keyed off
``event_metadata["agent_run_id"]`` for the v2 sub-agents and via the
``feature`` enum for the orchestrator's own calls). This module
denormalises that into the decision row so dashboards can surface
"prompt bloat showed up today" without joining on every query.

Pre-pilot scale: a cycle is tens of usage events, aggregation is a
single indexed query. Trivial cost.

Shape returned:

  {
    "input_tokens": 12340,
    "output_tokens": 480,
    "cache_read_tokens": 9800,
    "cache_creation_tokens": 1200,
    "total_micro_usd": 215000,
    "by_agent": {
      "<feature>": {"calls": N, "input": ..., "output": ..., "micro_usd": ...}
    }
  }

Failures degrade to ``{}`` so the decision can still be queued. Never
raises.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from ..models.usage_event import UsageEvent


logger = logging.getLogger("taali.agent_runtime.token_spend_aggregator")


def aggregate(
    db: Session, *, agent_run_id: int | None
) -> dict[str, Any]:
    """Sum usage_events for the given agent_run_id into a JSON roll-up.

    Returns ``{}`` when ``agent_run_id`` is None, no events match, or
    anything raises. The caller stuffs the result onto
    ``AgentDecision.token_spend``.

    Lookup is a broad ``LIKE`` prefilter narrowed by an exact match on
    the parsed ``agent_run_id`` (see ``_fetch_events``).
    """
    if agent_run_id is None:
        return {}
    try:
        rows = _fetch_events(db, agent_run_id=int(agent_run_id))
    except Exception as exc:
        logger.warning(
            "token_spend aggregate failed agent_run_id=%s error_type=%s",
            agent_run_id,
            type(exc).__name__,
        )
        return {}
    if not rows:
        return {}

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_micro_usd": 0,
    }
    by_agent: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "input": 0, "output": 0, "micro_usd": 0}
    )
    for row in rows:
        totals["input_tokens"] += int(row.input_tokens or 0)
        totals["output_tokens"] += int(row.output_tokens or 0)
        totals["cache_read_tokens"] += int(row.cache_read_tokens or 0)
        totals["cache_creation_tokens"] += int(row.cache_creation_tokens or 0)
        totals["total_micro_usd"] += int(row.cost_usd_micro or 0)
        feature_key = str(row.feature or "other")
        bucket = by_agent[feature_key]
        bucket["calls"] += 1
        bucket["input"] += int(row.input_tokens or 0)
        bucket["output"] += int(row.output_tokens or 0)
        bucket["micro_usd"] += int(row.cost_usd_micro or 0)
    return {**totals, "by_agent": dict(by_agent)}


def _fetch_events(
    db: Session, *, agent_run_id: int
) -> list[UsageEvent]:
    """Lookup ``usage_events`` for exactly this ``agent_run_id``.

    A broad ``LIKE`` over the serialised JSON narrows the scan (works on
    both Postgres and the SQLite test DB), then each candidate is matched
    on its parsed ``agent_run_id``. The exact post-filter is essential: a
    substring ``LIKE '%"agent_run_id": 12%'`` alone also matches ``120``
    and ``123``, folding other runs' spend into this roll-up.
    """
    from sqlalchemy import cast, String

    candidates = (
        db.query(UsageEvent)
        .filter(
            cast(UsageEvent.event_metadata, String).like(
                f'%"agent_run_id": {agent_run_id}%'
            )
        )
        .all()
    )
    return [r for r in candidates if _event_run_id(r) == agent_run_id]


def _event_run_id(row: UsageEvent) -> int | None:
    """Parse ``agent_run_id`` off a usage event's metadata, or None."""
    meta = row.event_metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            return None
    if not isinstance(meta, dict):
        return None
    raw = meta.get("agent_run_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["aggregate"]
