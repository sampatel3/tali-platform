"""Persistence and projection primitives for application pipeline events."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from .pipeline_event_history import (
    RoleApplicationActivity,
    event_metadata_id,
    latest_role_application_activity,
    membership_active_at_event,
    positive_int_hint,
    resolve_historical_event_role_id,
)


def event_to_payload(
    event: CandidateApplicationEvent,
    *,
    resolved_role_id: int | None = None,
) -> dict[str, Any]:
    return {
        "id": event.id,
        "application_id": event.application_id,
        "organization_id": event.organization_id,
        "role_id": resolved_role_id or event.role_id,
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
    requested_application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.id == int(application_id),
        )
        .one_or_none()
    )
    if requested_application is None:
        return []

    memberships = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(organization_id),
            SisterRoleEvaluation.role_id == int(role_id),
            SisterRoleEvaluation.candidate_id
            == int(requested_application.candidate_id),
            or_(
                SisterRoleEvaluation.source_application_id == int(application_id),
                SisterRoleEvaluation.ats_application_id == int(application_id),
            ),
        )
        .all()
    )
    linked_application_ids = {int(application_id)}
    for membership in memberships:
        linked_application_ids.add(int(membership.source_application_id))
        if membership.ats_application_id is not None:
            linked_application_ids.add(int(membership.ats_application_id))
    applications_by_id = {
        int(item.id): item
        for item in db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.id.in_(sorted(linked_application_ids)),
        )
        .all()
    }

    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id.in_(
                sorted(applications_by_id) or [-1]
            ),
            or_(
                CandidateApplicationEvent.role_id == int(role_id),
                CandidateApplicationEvent.role_id.is_(None),
            ),
        )
        .order_by(
            CandidateApplicationEvent.created_at.desc(),
            CandidateApplicationEvent.id.desc(),
        )
        .all()
    )
    null_role_rows = [event for event in rows if event.role_id is None]
    decisions_by_id: dict[int, Any] = {}
    decision_applications: dict[int, CandidateApplication] = {}
    valid_role_ids = {
        int(item.role_id) for item in applications_by_id.values()
    }.union({int(role_id)})
    if null_role_rows:
        hinted_decision_ids = {
            decision_id
            for event in null_role_rows
            for decision_id in [
                event_metadata_id(event, "agent_decision_id", "decision_id")
            ]
            if decision_id is not None
        }
        from ...models.agent_decision import AgentDecision

        decisions_by_id = {
            int(decision.id): decision
            for decision in db.query(AgentDecision)
            .filter(
                AgentDecision.organization_id == int(organization_id),
                AgentDecision.id.in_(sorted(hinted_decision_ids) or [-1]),
            )
            .all()
        }
        decision_application_ids = sorted(
            {int(decision.application_id) for decision in decisions_by_id.values()}
        )
        decision_applications = {
            int(item.id): item
            for item in db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.id.in_(decision_application_ids or [-1]),
            )
            .all()
        }
        valid_role_ids.update(
            role_hint
            for event in null_role_rows
            for role_hint in [event_metadata_id(event, "acting_role_id", "role_id")]
            if role_hint is not None
        )
        valid_role_ids.update(
            int(decision.role_id) for decision in decisions_by_id.values()
        )
        valid_role_ids = {
            int(valid_role_id)
            for (valid_role_id,) in db.query(Role.id)
            .filter(
                Role.organization_id == int(organization_id),
                Role.id.in_(sorted(valid_role_ids) or [-1]),
            )
            .all()
        }

    selected: list[dict[str, Any]] = []
    for event in rows:
        event_role_id = resolve_historical_event_role_id(
            event,
            application=applications_by_id.get(int(event.application_id)),
            memberships=memberships,
            decisions_by_id=decisions_by_id,
            decision_applications=decision_applications,
            valid_role_ids=valid_role_ids,
        )
        if event_role_id != int(role_id):
            continue
        selected.append(event_to_payload(event, resolved_role_id=event_role_id))
    return selected[int(offset) : int(offset) + int(limit)]


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


def _resolved_event_target(
    *,
    event_type: str,
    target_stage: str | None,
    to_stage: str | None,
    to_outcome: str | None,
    metadata: dict[str, Any],
) -> str | None:
    """Keep logical pipeline targets separate from ATS transport targets.

    A Tali transition to ``advanced`` and a provider move to ``Technical
    Interview`` are two independently observable effects.  The provider target
    may be present in pipeline-event metadata as requested intent, but it must
    never become the pipeline event's confirmed target.
    """

    event_key = str(event_type or "").strip().lower()
    if event_key in {"pipeline_stage_changed", "role_pipeline_stage_changed"}:
        value = to_stage
    elif event_key in {
        "application_outcome_changed",
        "role_application_outcome_changed",
    }:
        value = to_outcome
    else:
        value = (
            target_stage
            or metadata.get("target_stage")
            or metadata.get("workable_target_stage")
            or metadata.get("bullhorn_status")
            or to_stage
            or to_outcome
        )
    return str(value or "").strip() or None


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
    resolved_target = _resolved_event_target(
        event_type=event_type,
        target_stage=target_stage,
        to_stage=to_stage,
        to_outcome=to_outcome,
        metadata=event_metadata,
    )
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
    "RoleApplicationActivity",
    "append_event",
    "event_to_payload",
    "existing_idempotent_event",
    "event_metadata_id",
    "list_application_events",
    "latest_role_application_activity",
    "membership_active_at_event",
    "positive_int_hint",
    "resolve_historical_event_role_id",
]
