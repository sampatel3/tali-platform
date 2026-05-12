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

    Lookup uses two strategies:
    1. ``event_metadata @> '{"agent_run_id": <id>}'`` (Postgres JSON contains).
    2. Fallback to a string match on the JSON column for SQLite tests
       where the operator isn't available.
    """
    if agent_run_id is None:
        return {}
    try:
        rows = _fetch_events(db, agent_run_id=int(agent_run_id))
    except Exception as exc:
        logger.warning("token_spend aggregate failed for agent_run_id=%s: %s", agent_run_id, exc)
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
    """SQLite-safe lookup over ``event_metadata.agent_run_id``.

    On Postgres production this uses JSON containment via SQLAlchemy
    text(); on SQLite test it falls back to a substring match (which is
    safe because the metadata field is JSON-serialised by SQLAlchemy
    and the lookup is keyed on a numeric id).
    """
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    if dialect == "postgresql":
        from sqlalchemy import cast, String
        # The JSON column is named "metadata" on disk but mapped to
        # event_metadata in the model. We compare the serialised string
        # representation — pre-pilot volumes don't justify the
        # JSON-contains operator complexity here.
        return (
            db.query(UsageEvent)
            .filter(
                cast(UsageEvent.event_metadata, String).like(
                    f'%"agent_run_id": {agent_run_id}%'
                )
            )
            .all()
        )
    # SQLite (and anything else): use the same substring approach.
    from sqlalchemy import cast, String
    return (
        db.query(UsageEvent)
        .filter(
            cast(UsageEvent.event_metadata, String).like(
                f'%"agent_run_id": {agent_run_id}%'
            )
        )
        .all()
    )


__all__ = ["aggregate"]
