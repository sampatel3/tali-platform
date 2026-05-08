"""Aggregate learning signals for the nightly retune.

Three sources flow through one pipe:

  1. ``decision_feedback`` rows (explicit teach actions). Weight: 1.0
     by default; per-org overridable via
     ``Organization.workspace_settings.decision_policy_signal_weights``.
  2. ``agent_decisions`` with ``human_disposition='overridden'`` and no
     attached ``decision_feedback`` row (silent overrides). Weight: 0.3.
  3. Manual recruiter ``CandidateApplicationEvent``s (per §5.2 of
     AGENTIC_DECISION_SYSTEM.md). Weight: 0.8. Each is run through
     ``retroactive_eval`` to compute a disagreement_pattern; agreement
     events are dropped.

Returns a uniform list of ``Signal`` records the retuner consumes
without caring which source produced them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.decision_feedback import DecisionFeedback
from ..models.organization import Organization
from ..models.rubric_revision import RubricRevision
from .retroactive_eval import disagreement_for_manual_event


logger = logging.getLogger("taali.decision_policy.aggregator")


# Defaults match Sam's resolved decisions (§10 in CLAUDE.md). Lives in
# ``Organization.workspace_settings.decision_policy_signal_weights`` so
# Sam can tune per-org without a code change.
DEFAULT_SIGNAL_WEIGHTS: dict[str, float] = {
    "teach": 1.0,
    "manual": 0.8,
    "override": 0.3,
}


@dataclass
class Signal:
    """One learning signal in the uniform shape the retuner consumes.

    ``disagreement_pattern`` is one of (per §5.2 of the design doc):
      - manual-send-on-would-reject
      - manual-reject-on-would-send
      - manual-advance-on-would-reject-post-assessment
      - manual-reject-on-would-advance
      - failure-mode:<failure_mode>     (from explicit teach actions)
      - silent-override                  (from human_disposition='overridden')
    """

    signal_type: str  # 'teach' | 'manual' | 'override'
    weight: float
    disagreement_pattern: str
    source_id: int
    decision_point: str | None = None
    failure_mode: str | None = None
    correction_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedSignals:
    organization_id: int
    since: datetime
    signals: list[Signal] = field(default_factory=list)
    teach_count: int = 0
    manual_count: int = 0
    override_count: int = 0

    @property
    def total_weighted(self) -> float:
        return sum(s.weight for s in self.signals)

    @property
    def feedback_ids(self) -> list[int]:
        return sorted(
            {
                int(s.source_id)
                for s in self.signals
                if s.signal_type == "teach"
            }
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal_weights_for(org: Organization) -> dict[str, float]:
    settings = (
        org.workspace_settings
        if isinstance(org.workspace_settings, dict)
        else None
    )
    overrides = (
        (settings or {}).get("decision_policy_signal_weights")
        if isinstance(settings, dict)
        else None
    )
    base = dict(DEFAULT_SIGNAL_WEIGHTS)
    if isinstance(overrides, dict):
        for key in DEFAULT_SIGNAL_WEIGHTS:
            v = overrides.get(key)
            if isinstance(v, (int, float)):
                base[key] = float(v)
    return base


def _last_retune_cutoff(
    db: Session, *, organization_id: int, fallback: timedelta
) -> datetime:
    last = (
        db.query(RubricRevision)
        .filter(
            RubricRevision.organization_id == organization_id,
            RubricRevision.cause == "feedback_retune",
        )
        .order_by(RubricRevision.created_at.desc())
        .first()
    )
    if last is not None and last.created_at is not None:
        ts = last.created_at
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return datetime.now(timezone.utc) - fallback


# ---------------------------------------------------------------------------
# Source pulls
# ---------------------------------------------------------------------------


def _pull_explicit_feedback(
    db: Session,
    *,
    organization_id: int,
    since: datetime,
    weight: float,
) -> list[Signal]:
    rows = (
        db.query(DecisionFeedback)
        .filter(
            DecisionFeedback.organization_id == organization_id,
            DecisionFeedback.created_at >= since,
            # Co-signed-org rule: scope='org' rows only count once
            # cosigned_at is non-null (per cosign_required handling).
        )
        .all()
    )
    out: list[Signal] = []
    for row in rows:
        if row.scope == "org" and bool(row.cosign_required) and row.cosigned_at is None:
            # Awaiting co-sign — defer.
            continue
        out.append(
            Signal(
                signal_type="teach",
                weight=weight,
                disagreement_pattern=f"failure-mode:{row.failure_mode}",
                source_id=int(row.id),
                failure_mode=str(row.failure_mode),
                correction_text=row.correction_text,
                decision_point=None,
                metadata={"scope": str(row.scope)},
            )
        )
    return out


def _pull_silent_overrides(
    db: Session,
    *,
    organization_id: int,
    since: datetime,
    weight: float,
) -> list[Signal]:
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.human_disposition == "overridden",
            AgentDecision.resolved_at.isnot(None),
            AgentDecision.resolved_at >= since,
        )
        .all()
    )
    out: list[Signal] = []
    for row in rows:
        # Skip when there's an attached teach feedback row — that
        # gets the higher 'teach' weight already.
        if row.feedback_id is not None:
            continue
        out.append(
            Signal(
                signal_type="override",
                weight=weight,
                disagreement_pattern="silent-override",
                source_id=int(row.id),
                decision_point=str(row.decision_type),
                metadata={
                    "override_action": row.override_action,
                    "resolution_note": row.resolution_note,
                },
            )
        )
    return out


def _pull_manual_actions(
    db: Session,
    *,
    organization_id: int,
    since: datetime,
    weight: float,
) -> list[Signal]:
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == organization_id,
            CandidateApplicationEvent.actor_type == "recruiter",
            CandidateApplicationEvent.created_at >= since,
        )
        .all()
    )
    out: list[Signal] = []
    for ev in rows:
        try:
            disagreement = disagreement_for_manual_event(db, event=ev)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("retroactive_eval crashed for event %s: %s", ev.id, exc)
            disagreement = None
        if disagreement is None or disagreement.pattern == "agreement":
            continue
        out.append(
            Signal(
                signal_type="manual",
                weight=weight,
                disagreement_pattern=disagreement.pattern,
                source_id=int(ev.id),
                decision_point=disagreement.decision_point,
                metadata={
                    "event_type": ev.event_type,
                    "to_stage": ev.to_stage,
                    "to_outcome": ev.to_outcome,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def aggregate_signals(
    db: Session,
    *,
    organization_id: int,
    since: datetime | None = None,
    fallback_lookback: timedelta = timedelta(days=14),
) -> AggregatedSignals:
    """Pull all three signal sources for one org.

    ``since`` defaults to the timestamp of the most recent
    ``cause='feedback_retune'`` revision; if none, falls back
    ``fallback_lookback`` into the past so first-time runs catch a
    sensible window.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .one_or_none()
    )
    if org is None:
        raise LookupError(f"organization {organization_id} not found")

    if since is None:
        since = _last_retune_cutoff(
            db, organization_id=organization_id, fallback=fallback_lookback
        )

    weights = _signal_weights_for(org)

    teach_signals = _pull_explicit_feedback(
        db,
        organization_id=organization_id,
        since=since,
        weight=weights["teach"],
    )
    override_signals = _pull_silent_overrides(
        db,
        organization_id=organization_id,
        since=since,
        weight=weights["override"],
    )
    manual_signals = _pull_manual_actions(
        db,
        organization_id=organization_id,
        since=since,
        weight=weights["manual"],
    )

    return AggregatedSignals(
        organization_id=organization_id,
        since=since,
        signals=teach_signals + override_signals + manual_signals,
        teach_count=len(teach_signals),
        manual_count=len(manual_signals),
        override_count=len(override_signals),
    )


__all__ = [
    "AggregatedSignals",
    "DEFAULT_SIGNAL_WEIGHTS",
    "Signal",
    "aggregate_signals",
]
