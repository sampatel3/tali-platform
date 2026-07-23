"""Reads recent recruiter events for an application.

Used by the orchestrator to populate ``DecisionInputs.manual_actions``
so the policy can skip decision points the recruiter has already
handled (per §5.1 of AGENTIC_DECISION_SYSTEM.md).

Pure read — no mutations. Pure-Python ``ManualAction`` shape — no
SQLAlchemy rows leak into the engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..decision_policy.engine import ManualAction
from ..models.candidate_application_event import CandidateApplicationEvent


# Map raw event_type values to the four ``ManualAction.kind`` buckets
# the engine reasons about. Anything not mapped is ignored.
EVENT_TYPE_TO_KIND: dict[str, str] = {
    # Sending an assessment invite — agent or recruiter both fire this,
    # but we filter by ``actor_type='recruiter'`` below so only manual
    # sends count as a manual action.
    "assessment_invite_sent": "sent_assessment",
    "assessment_invite_resent": "sent_assessment",
    "assessment_retake_sent": "sent_assessment",
    # Rejection — explicit reject action or pipeline outcome flip to
    # ``rejected``.
    "auto_rejected": "rejected",
    "workable_disqualified": "rejected",
    "application_outcome_changed": "outcome_changed",
    # Stage advances — pipeline_stage_changed events carry the new
    # ``to_stage``; we resolve advance vs. demote downstream.
    "pipeline_stage_changed": "stage_changed",
}


# Stages that count as "advanced past assessment". A move into one of
# these from a pre-assessment stage is treated as an advance action.
ADVANCED_STAGES = frozenset(
    {"advanced", "technical_interview", "interview", "offer", "hired"}
)


def _is_advanced_outcome(event: CandidateApplicationEvent) -> bool:
    """When ``application_outcome_changed`` is the event, treat
    ``hired`` as an advance and ``rejected`` as a reject."""
    return (event.to_outcome or "").lower() in {"hired"}


def _is_rejection_outcome(event: CandidateApplicationEvent) -> bool:
    return (event.to_outcome or "").lower() in {"rejected"}


def _classify_stage_change(
    event: CandidateApplicationEvent,
) -> str | None:
    to_stage = (event.to_stage or "").lower()
    if to_stage in ADVANCED_STAGES:
        return "advanced"
    return None


def _to_manual_action(event: CandidateApplicationEvent) -> ManualAction | None:
    raw_kind = EVENT_TYPE_TO_KIND.get(event.event_type)
    if raw_kind is None:
        return None
    if raw_kind == "stage_changed":
        normalized = _classify_stage_change(event)
        if normalized is None:
            return None
        kind = normalized
    elif raw_kind == "outcome_changed":
        if _is_rejection_outcome(event):
            kind = "rejected"
        elif _is_advanced_outcome(event):
            kind = "advanced_outcome"
        else:
            return None
    else:
        kind = raw_kind
    ts = event.created_at
    if ts is None:
        ts_iso = ""
    else:
        ts_iso = (
            ts.replace(tzinfo=timezone.utc).isoformat()
            if ts.tzinfo is None
            else ts.isoformat()
        )
    return ManualAction(
        kind=kind,
        timestamp_iso=ts_iso,
        actor_id=int(event.actor_id) if event.actor_id is not None else None,
        reason=event.reason,
    )


def read_recent_manual_actions(
    db: Session,
    *,
    application_id: int,
    role_id: int,
    lookback_hours: int,
    now: datetime | None = None,
) -> list[ManualAction]:
    """Pull role-owned recruiter events within the lookback window.

    Returns a list ordered most-recent-first. ``actor_type`` strictly
    equals ``'recruiter'`` — the agent's own queueing/cancellations are
    not manual actions. First-class event provenance is required: ambiguous
    legacy NULL-role rows cannot safely suppress an autonomous decision for
    any logical role.
    """
    if lookback_hours <= 0:
        return []
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=int(lookback_hours))
    rows: Iterable[CandidateApplicationEvent] = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.role_id == int(role_id),
            CandidateApplicationEvent.actor_type == "recruiter",
            CandidateApplicationEvent.created_at >= cutoff,
        )
        .order_by(CandidateApplicationEvent.created_at.desc())
        .all()
    )
    actions: list[ManualAction] = []
    for ev in rows:
        action = _to_manual_action(ev)
        if action is not None:
            actions.append(action)
    return actions


__all__ = [
    "ADVANCED_STAGES",
    "EVENT_TYPE_TO_KIND",
    "read_recent_manual_actions",
]
