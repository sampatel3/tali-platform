"""Historical role attribution and latest-activity queries for pipeline events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...services.logical_event_membership import apply_live_logical_event_scope


def positive_int_hint(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def event_metadata_id(
    event: CandidateApplicationEvent,
    *keys: str,
) -> int | None:
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    for key in keys:
        parsed = positive_int_hint(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def membership_active_at_event(
    membership: SisterRoleEvaluation,
    event_time: datetime | None,
) -> bool:
    """Whether a historical membership owned the candidate when an event occurred."""

    if not isinstance(event_time, datetime):
        return False
    event_time = (
        event_time.replace(tzinfo=timezone.utc)
        if event_time.tzinfo is None
        else event_time.astimezone(timezone.utc)
    )
    created_at = membership.created_at
    deleted_at = membership.deleted_at
    if isinstance(created_at, datetime):
        created_at = (
            created_at.replace(tzinfo=timezone.utc)
            if created_at.tzinfo is None
            else created_at.astimezone(timezone.utc)
        )
    if isinstance(deleted_at, datetime):
        deleted_at = (
            deleted_at.replace(tzinfo=timezone.utc)
            if deleted_at.tzinfo is None
            else deleted_at.astimezone(timezone.utc)
        )
    # Migration 185 materializes the previously implicit owner-role pool at
    # cutover time. Those compatibility rows necessarily post-date genuine
    # legacy events; every other membership must already have existed.
    created_in_time = (
        str(membership.membership_source or "") == "legacy_implicit_snapshot"
        or (isinstance(created_at, datetime) and created_at <= event_time)
    )
    return created_in_time and (
        not isinstance(deleted_at, datetime) or event_time <= deleted_at
    )


def resolve_historical_event_role_id(
    event: CandidateApplicationEvent,
    *,
    application: CandidateApplication | None,
    memberships: list[SisterRoleEvaluation],
    decisions_by_id: dict[int, Any],
    decision_applications: dict[int, CandidateApplication],
    valid_role_ids: set[int],
) -> int | None:
    """Resolve one immutable pre-provenance event without rewriting evidence."""

    if event.role_id is not None:
        return int(event.role_id)
    if application is None:
        return None
    application_id = int(application.id)
    candidate_id = int(application.candidate_id)

    def role_authorized(role_id: int | None) -> bool:
        if role_id is None or role_id not in valid_role_ids:
            return False
        if role_id == int(application.role_id):
            return True
        return any(
            int(membership.role_id) == role_id
            and int(membership.candidate_id) == candidate_id
            and membership_active_at_event(membership, event.created_at)
            and application_id
            in {
                int(membership.source_application_id),
                *(
                    [int(membership.ats_application_id)]
                    if membership.ats_application_id is not None
                    else []
                ),
            }
            for membership in memberships
        )

    metadata_role_id = event_metadata_id(event, "acting_role_id", "role_id")
    if role_authorized(metadata_role_id):
        return metadata_role_id

    decision_id = event_metadata_id(event, "agent_decision_id", "decision_id")
    decision = decisions_by_id.get(decision_id) if decision_id is not None else None
    if decision is not None:
        decision_application = decision_applications.get(int(decision.application_id))
        decision_role_id = int(decision.role_id)
        if (
            decision_application is not None
            and int(decision_application.candidate_id) == candidate_id
            and role_authorized(decision_role_id)
        ):
            return decision_role_id
    return int(application.role_id)


@dataclass(frozen=True)
class RoleApplicationActivity:
    """One role-owned event with its canonical logical application identity."""

    event: CandidateApplicationEvent
    application_id: int
    candidate: Candidate | None


def latest_role_application_activity(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    actor_types: tuple[str, ...] = ("agent", "recruiter"),
) -> RoleApplicationActivity | None:
    """Return the latest event attributable to one logical role.

    First-class ``event.role_id`` is authoritative. Legacy NULL-role events are
    considered only on direct role applications or explicit historical related
    memberships, then resolved with the same occurrence-time authority used by
    the audit/event API. Owner-pool rows without membership never enter scope.
    """

    memberships = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(organization_id),
            SisterRoleEvaluation.role_id == int(role_id),
        )
        .all()
    )
    linked_application_ids = {
        application_id
        for membership in memberships
        for application_id in (
            int(membership.source_application_id),
            (
                int(membership.ats_application_id)
                if membership.ats_application_id is not None
                else None
            ),
        )
        if application_id is not None
    }
    linked_application_ids.update(
        int(application_id)
        for (application_id,) in db.query(CandidateApplication.id)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.role_id == int(role_id),
        )
        .all()
    )

    rows_query = (
        db.query(CandidateApplicationEvent, CandidateApplication, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == int(organization_id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplicationEvent.actor_type.in_(actor_types),
            or_(
                CandidateApplicationEvent.role_id == int(role_id),
                and_(
                    CandidateApplicationEvent.role_id.is_(None),
                    CandidateApplicationEvent.application_id.in_(
                        sorted(linked_application_ids) or [-1]
                    ),
                ),
            ),
        )
    )
    rows = (
        apply_live_logical_event_scope(
            db,
            rows_query,
            organization_id=int(organization_id),
        )
        .order_by(
            CandidateApplicationEvent.created_at.desc(),
            CandidateApplicationEvent.id.desc(),
        )
        .all()
    )
    if not rows:
        return None

    applications_by_id = {
        int(application.id): application for _, application, _ in rows
    }
    for membership in memberships:
        for application in (membership.source_application, membership.ats_application):
            if application is not None:
                applications_by_id.setdefault(int(application.id), application)

    null_events = [event for event, _, _ in rows if event.role_id is None]
    hinted_decision_ids = {
        decision_id
        for event in null_events
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
    decision_application_ids = {
        int(decision.application_id) for decision in decisions_by_id.values()
    }
    decision_applications = {
        int(application.id): application
        for application in db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.id.in_(sorted(decision_application_ids) or [-1]),
        )
        .all()
    }
    valid_role_hints = {
        int(role_id),
        *[int(application.role_id) for application in applications_by_id.values()],
        *[int(decision.role_id) for decision in decisions_by_id.values()],
        *[
            hint
            for event in null_events
            for hint in [event_metadata_id(event, "acting_role_id", "role_id")]
            if hint is not None
        ],
    }
    valid_role_ids = {
        int(valid_role_id)
        for (valid_role_id,) in db.query(Role.id)
        .filter(
            Role.organization_id == int(organization_id),
            Role.id.in_(sorted(valid_role_hints)),
        )
        .all()
    }

    for event, application, candidate in rows:
        resolved_role_id = resolve_historical_event_role_id(
            event,
            application=application,
            memberships=memberships,
            decisions_by_id=decisions_by_id,
            decision_applications=decision_applications,
            valid_role_ids=valid_role_ids,
        )
        if resolved_role_id != int(role_id):
            continue
        active_memberships = [
            membership
            for membership in memberships
            if int(membership.candidate_id) == int(application.candidate_id)
            and int(event.application_id)
            in {
                int(membership.source_application_id),
                *(
                    [int(membership.ats_application_id)]
                    if membership.ats_application_id is not None
                    else []
                ),
            }
            and membership_active_at_event(membership, event.created_at)
        ]
        canonical_application_id = (
            int(active_memberships[0].source_application_id)
            if active_memberships
            else int(event.application_id)
        )
        return RoleApplicationActivity(
            event=event,
            application_id=canonical_application_id,
            candidate=candidate,
        )
    return None


__all__ = [
    "RoleApplicationActivity",
    "event_metadata_id",
    "latest_role_application_activity",
    "membership_active_at_event",
    "positive_int_hint",
    "resolve_historical_event_role_id",
]
