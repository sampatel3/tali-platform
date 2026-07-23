"""Live logical-subject authority for recruiter-facing application events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Query, Session, aliased

from ..candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent


@dataclass(frozen=True)
class LiveLogicalEventScope:
    """Reusable live-subject boundary for application-event product reads."""

    organization_id: int
    membership: Any

    def apply(self, query: Query) -> Query:
        event_application = aliased(
            CandidateApplication,
            name="live_event_source_application",
        )
        subject_application = aliased(
            CandidateApplication,
            name="live_event_subject_application",
        )
        subject_candidate = aliased(
            Candidate,
            name="live_event_subject_candidate",
        )
        event_logical_role_id = func.coalesce(
            CandidateApplicationEvent.role_id,
            CandidateApplicationEvent.event_metadata[
                "acting_role_id"
            ].as_integer(),
            CandidateApplicationEvent.event_metadata["role_id"].as_integer(),
            event_application.role_id,
        )
        live_subject = (
            select(self.membership.c.application_id)
            .select_from(self.membership)
            .join(
                subject_application,
                subject_application.id == self.membership.c.application_id,
            )
            .join(
                subject_candidate,
                subject_candidate.id == subject_application.candidate_id,
            )
            .join(
                event_application,
                event_application.id == CandidateApplicationEvent.application_id,
            )
            .where(
                event_application.organization_id == self.organization_id,
                subject_application.organization_id == self.organization_id,
                subject_application.candidate_id == event_application.candidate_id,
                subject_candidate.organization_id == self.organization_id,
                subject_candidate.deleted_at.is_(None),
                self.membership.c.logical_role_id == event_logical_role_id,
            )
            .correlate(CandidateApplicationEvent)
            .exists()
        )
        return query.filter(
            CandidateApplicationEvent.organization_id == self.organization_id,
            live_subject,
        )


def resolve_live_logical_event_scope(
    db: Session,
    *,
    organization_id: int,
) -> LiveLogicalEventScope:
    """Resolve the organization's current logical roster once."""

    organization_id = int(organization_id)
    selection = resolve_logical_application_selection(
        db,
        organization_id=organization_id,
        role_ids=(),
    )
    return LiveLogicalEventScope(
        organization_id=organization_id,
        membership=selection.membership_rows,
    )


def apply_live_logical_event_scope(
    db: Session,
    query: Query,
    *,
    organization_id: int,
) -> Query:
    """Hide events whose person or logical role membership is no longer live.

    The event ledger remains immutable. Recruiter-facing activity views may
    project a historical row only while the same person is still a member of
    the event's logical role. This follows related-role history across a source
    evidence replacement without reviving a removed membership.
    """

    return resolve_live_logical_event_scope(
        db,
        organization_id=int(organization_id),
    ).apply(query)


__all__ = [
    "apply_live_logical_event_scope",
    "LiveLogicalEventScope",
    "resolve_live_logical_event_scope",
]
