"""Live logical-subject authority for recruiter-facing agent questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import Query, Session, aliased

from ..candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from ..models.agent_needs_input import (
    CANDIDATE_APPLICATION_SUBJECT_KINDS,
    AgentNeedsInput,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role


def is_candidate_application_subject(*, kind: str, subject_id: int | None) -> bool:
    """Return whether structured fields identify a candidate application."""

    return (
        subject_id is not None
        and str(kind) in CANDIDATE_APPLICATION_SUBJECT_KINDS
    )


@dataclass(frozen=True)
class LiveLogicalNeedsInputScope:
    """Reusable lifecycle boundary for candidate-scoped recruiter questions."""

    organization_id: int
    membership: Any

    def _live_subject_exists(self, *, role_id: Any, application_id: Any) -> Any:
        subject_application = aliased(
            CandidateApplication,
            name="live_needs_input_subject_application",
        )
        subject_candidate = aliased(
            Candidate,
            name="live_needs_input_subject_candidate",
        )
        return (
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
            .where(
                self.membership.c.logical_role_id == role_id,
                self.membership.c.application_id == application_id,
                subject_application.organization_id == self.organization_id,
                subject_candidate.organization_id == self.organization_id,
                subject_candidate.deleted_at.is_(None),
            )
            .correlate(AgentNeedsInput)
            .exists()
        )

    def apply(self, query: Query) -> Query:
        """Keep general rows and only live candidate-application subjects."""

        candidate_subject = and_(
            AgentNeedsInput.kind.in_(CANDIDATE_APPLICATION_SUBJECT_KINDS),
            AgentNeedsInput.subject_id.isnot(None),
        )
        live_role = (
            select(Role.id)
            .where(
                Role.id == AgentNeedsInput.role_id,
                Role.organization_id == self.organization_id,
                Role.deleted_at.is_(None),
            )
            .correlate(AgentNeedsInput)
            .exists()
        )
        return query.filter(
            AgentNeedsInput.organization_id == self.organization_id,
            live_role,
            or_(
                not_(candidate_subject),
                self._live_subject_exists(
                    role_id=AgentNeedsInput.role_id,
                    application_id=AgentNeedsInput.subject_id,
                ),
            ),
        )

    def query(self, db: Session, *entities: Any) -> Query:
        return self.apply(db.query(*entities))

    def subject_is_live(self, db: Session, *, role_id: int, application_id: int) -> bool:
        """Authorize a structured subject before opening a new question."""

        subject_application = aliased(
            CandidateApplication,
            name="new_needs_input_subject_application",
        )
        subject_candidate = aliased(
            Candidate,
            name="new_needs_input_subject_candidate",
        )
        row = (
            db.query(self.membership.c.application_id)
            .join(
                subject_application,
                subject_application.id == self.membership.c.application_id,
            )
            .join(
                subject_candidate,
                subject_candidate.id == subject_application.candidate_id,
            )
            .filter(
                self.membership.c.logical_role_id == int(role_id),
                self.membership.c.application_id == int(application_id),
                subject_application.organization_id == self.organization_id,
                subject_candidate.organization_id == self.organization_id,
                subject_candidate.deleted_at.is_(None),
            )
            .first()
        )
        return row is not None


def resolve_live_logical_needs_input_scope(
    db: Session,
    *,
    organization_id: int,
) -> LiveLogicalNeedsInputScope:
    """Resolve one organization's canonical logical roster."""

    organization_id = int(organization_id)
    selection = resolve_logical_application_selection(
        db,
        organization_id=organization_id,
        role_ids=(),
    )
    return LiveLogicalNeedsInputScope(
        organization_id=organization_id,
        membership=selection.membership_rows,
    )


def apply_live_logical_needs_input_scope(
    db: Session,
    query: Query,
    *,
    organization_id: int,
) -> Query:
    """Apply the candidate lifecycle boundary while preserving general rows."""

    return resolve_live_logical_needs_input_scope(
        db,
        organization_id=int(organization_id),
    ).apply(query)


__all__ = [
    "apply_live_logical_needs_input_scope",
    "is_candidate_application_subject",
    "LiveLogicalNeedsInputScope",
    "resolve_live_logical_needs_input_scope",
]
