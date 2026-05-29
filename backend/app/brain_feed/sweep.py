"""Gather source rows and enqueue anonymized brain-feed payloads.

Decoupled from the live write paths on purpose: rather than hooking the hot
decision/teach/usage transactions, a periodic sweep scans recently-resolved
decisions, teach outcomes, and whole-day usage rollups and enqueues anything
not already in the outbox (idempotent on ``event_id``). That keeps the feed
entirely off the critical path of the live platform — a sweep failure can
never affect a recruiter action — at the cost of a short, bounded delay.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.decision_feedback import DecisionFeedback
from ..models.usage_event import UsageEvent
from ..platform.config import settings
from . import anonymize
from .outbox import enqueue


logger = logging.getLogger("taali.brain_feed.sweep")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _enqueue_resolved_decisions(db: Session, cutoff: datetime) -> int:
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.resolved_at.isnot(None),
            AgentDecision.resolved_at >= cutoff,
        )
        .order_by(AgentDecision.resolved_at.asc())
        .all()
    )
    n = 0
    for d in rows:
        if enqueue(
            db,
            record_kind="decision",
            event_id=anonymize.decision_event_id(d.id),
            payload=anonymize.decision_payload(d),
        ) is not None:
            n += 1
    return n


def _enqueue_outcomes(db: Session, cutoff: datetime) -> int:
    rows = (
        db.query(DecisionFeedback)
        .filter(DecisionFeedback.created_at >= cutoff)
        .order_by(DecisionFeedback.created_at.asc())
        .all()
    )
    n = 0
    for f in rows:
        if enqueue(
            db,
            record_kind="outcome",
            event_id=anonymize.outcome_event_id(f.id),
            payload=anonymize.outcome_payload(f),
        ) is not None:
            n += 1
    return n


def _enqueue_usage_rollups(db: Session, cutoff: datetime, today_start: datetime) -> int:
    """Aggregate usage_events into per-day, per-(feature, model) rollups.

    Only whole past days (created_at < today_start) are aggregated, so each
    bucket is final before it ships — re-sweeping the same day is a no-op.
    Aggregation is done in Python (not DB-side ``date()``) to stay portable
    across Postgres (prod) and SQLite (tests).
    """
    rows = (
        db.query(UsageEvent)
        .filter(UsageEvent.created_at >= cutoff, UsageEvent.created_at < today_start)
        .all()
    )
    buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    for u in rows:
        created = u.created_at
        if created is None:
            continue
        day = created.date().isoformat()
        key = (day, u.feature or "unknown", u.model or "unknown")
        agg = buckets.setdefault(
            key,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd_micro": 0,
                "event_count": 0,
            },
        )
        agg["input_tokens"] += int(u.input_tokens or 0)
        agg["output_tokens"] += int(u.output_tokens or 0)
        agg["cache_read_tokens"] += int(u.cache_read_tokens or 0)
        agg["cache_creation_tokens"] += int(u.cache_creation_tokens or 0)
        agg["cost_usd_micro"] += int(u.cost_usd_micro or 0)
        agg["event_count"] += 1

    n = 0
    for (day, feature, model), agg in buckets.items():
        payload = anonymize.usage_payload(day=day, feature=feature, model=model, **agg)
        if enqueue(
            db,
            record_kind="usage",
            event_id=anonymize.usage_event_id(day, feature, model),
            payload=payload,
        ) is not None:
            n += 1
    return n


def sweep_and_enqueue(db: Session, *, lookback_hours: int | None = None) -> dict:
    """Enqueue all new brain-feed records since the lookback window.

    No-op when the feature is disabled. Commits its own transaction (the
    enqueued rows). Idempotent: already-enqueued source rows are skipped on
    ``event_id``.
    """
    if not settings.MAINSPRING_BRAIN_FEED_ENABLED:
        return {"status": "disabled", "decisions": 0, "outcomes": 0, "usage": 0}

    hours = int(lookback_hours or settings.MAINSPRING_BRAIN_FEED_LOOKBACK_HOURS)
    now = _now()
    cutoff = now - timedelta(hours=hours)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    decisions = _enqueue_resolved_decisions(db, cutoff)
    outcomes = _enqueue_outcomes(db, cutoff)
    usage = _enqueue_usage_rollups(db, cutoff, today_start)
    db.commit()

    summary = {
        "status": "ok",
        "decisions": decisions,
        "outcomes": outcomes,
        "usage": usage,
    }
    if decisions or outcomes or usage:
        logger.info("brain_feed sweep enqueued: %s", summary)
    return summary


__all__ = ["sweep_and_enqueue"]
