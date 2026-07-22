"""Persistence and projection primitives for application pipeline events."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent


def event_to_payload(event: CandidateApplicationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "application_id": event.application_id,
        "organization_id": event.organization_id,
        "role_id": event.role_id,
        "agent_decision_id": event.agent_decision_id,
        "event_type": event.event_type,
        "from_stage": event.from_stage,
        "to_stage": event.to_stage,
        "from_outcome": event.from_outcome,
        "to_outcome": event.to_outcome,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "reason": event.reason,
        "target_stage": event.target_stage,
        "effect_status": event.effect_status,
        "metadata": event.event_metadata or {},
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at,
    }


def list_application_events(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    role_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == organization_id,
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.role_id == int(role_id),
        )
        .order_by(
            CandidateApplicationEvent.created_at.desc(),
            CandidateApplicationEvent.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [event_to_payload(item) for item in rows]


def existing_idempotent_event(
    db: Session,
    *,
    application_id: int,
    role_id: int,
    idempotency_key: str | None,
) -> CandidateApplicationEvent | None:
    token = str(idempotency_key or "").strip()
    if not token:
        return None
    return (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.role_id == int(role_id),
            CandidateApplicationEvent.idempotency_key == token,
        )
        .first()
    )


def append_event(
    db: Session,
    *,
    app: CandidateApplication,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    from_outcome: str | None = None,
    to_outcome: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    role_id: int | None = None,
    agent_decision_id: int | None = None,
    target_stage: str | None = None,
    effect_status: str | None = None,
) -> CandidateApplicationEvent:
    event_metadata = dict(metadata or {})

    def _metadata_int(*keys: str) -> int | None:
        for key in keys:
            value = event_metadata.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    logical_role_id = int(
        role_id or _metadata_int("acting_role_id", "role_id") or app.role_id
    )
    linked_decision_id = agent_decision_id or _metadata_int(
        "agent_decision_id", "decision_id"
    )
    resolved_target = str(
        target_stage
        or event_metadata.get("target_stage")
        or event_metadata.get("workable_target_stage")
        or to_stage
        or to_outcome
        or ""
    ).strip() or None
    resolved_effect = str(effect_status or "").strip().lower() or None
    if resolved_effect is None:
        event_key = str(event_type or "").strip().lower()
        if "failed" in event_key:
            resolved_effect = "failed"
        elif "skipped" in event_key:
            resolved_effect = "skipped"
        elif event_key in {
            "pipeline_stage_changed",
            "application_outcome_changed",
            "role_pipeline_stage_changed",
            "role_application_outcome_changed",
            "workable_moved",
            "bullhorn_moved",
            "workable_disqualified",
            "bullhorn_rejected",
            "assessment_invite_sent",
            "assessment_invite_resent",
        }:
            resolved_effect = "confirmed"
    event = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        role_id=logical_role_id,
        agent_decision_id=linked_decision_id,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        from_outcome=from_outcome,
        to_outcome=to_outcome,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        event_metadata=event_metadata or None,
        target_stage=resolved_target,
        effect_status=resolved_effect,
        idempotency_key=(str(idempotency_key or "").strip() or None),
    )
    db.add(event)
    return event


__all__ = [
    "append_event",
    "event_to_payload",
    "existing_idempotent_event",
    "list_application_events",
]
