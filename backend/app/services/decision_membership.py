"""Locked reads of an agent decision's current role membership."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session, aliased

from ..candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from ..models.agent_decision import (
    AGENT_DECISION_ACTIVE_STATUSES,
    AgentDecision,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from .role_concurrency import role_query_for_update


@dataclass(frozen=True)
class LiveLogicalDecisionScope:
    """Reusable live-subject authority for one request/organization."""

    organization_id: int
    membership: Any

    def apply(self, query: Query) -> Query:
        subject_application = aliased(
            CandidateApplication,
            name="live_decision_subject_application",
        )
        subject_candidate = aliased(
            Candidate,
            name="live_decision_subject_candidate",
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
            .where(
                self.membership.c.logical_role_id == AgentDecision.role_id,
                subject_application.organization_id == self.organization_id,
                subject_application.candidate_id == AgentDecision.candidate_id,
                subject_candidate.organization_id == self.organization_id,
                subject_candidate.deleted_at.is_(None),
                or_(
                    AgentDecision.status.notin_(AGENT_DECISION_ACTIVE_STATUSES),
                    self.membership.c.application_id
                    == AgentDecision.application_id,
                ),
            )
            .correlate(AgentDecision)
            .exists()
        )
        return query.filter(
            AgentDecision.organization_id == self.organization_id,
            live_subject,
        )

    def query(self, db: Session, *entities) -> Query:
        return self.apply(db.query(*entities))


def resolve_live_logical_decision_scope(
    db: Session,
    *,
    organization_id: int,
) -> LiveLogicalDecisionScope:
    """Resolve role storage semantics once for a bounded request."""

    organization_id = int(organization_id)
    selection = resolve_logical_application_selection(
        db,
        organization_id=organization_id,
        role_ids=(),
    )
    return LiveLogicalDecisionScope(
        organization_id=organization_id,
        membership=selection.membership_rows,
    )


def apply_live_logical_decision_scope(
    db: Session, query: Query, *, organization_id: int
) -> Query:
    """Restrict a recruiter-facing decision query to live logical subjects.

    Agent decisions are immutable audit records, but candidate-facing product
    surfaces may expose them only while the person is live and the acting role
    still owns that candidate.  Ownership is keyed by ``(role, candidate)``:
    a related role can replace its evidence application without changing the
    logical subject of a resolved prior decision. Active queue rows remain
    bound to their exact current membership application because that is the
    side-effect authority an approval would execute against.

    The membership subquery is the same authority used by candidate search:
    ordinary roles require a live application, while related roles require a
    live ``SisterRoleEvaluation``.  Keeping the check as a correlated
    ``EXISTS`` lets callers preserve their existing joins and projections.
    """

    return resolve_live_logical_decision_scope(
        db,
        organization_id=int(organization_id),
    ).apply(query)


def live_logical_decision_query(
    db: Session,
    *entities,
    organization_id: int,
) -> Query:
    """Create a query already constrained to the live decision boundary."""

    return apply_live_logical_decision_scope(
        db,
        db.query(*entities),
        organization_id=int(organization_id),
    )


def lock_role_memberships(
    db: Session,
    *,
    decision_ids: Iterable[int],
    organization_id: int,
) -> list[tuple[int, int | None]]:
    """Return current ``(decision_id, role_id)`` rows, locked on PostgreSQL."""
    query = db.query(AgentDecision.id, AgentDecision.role_id).filter(
        AgentDecision.id.in_([int(value) for value in decision_ids]),
        AgentDecision.organization_id == int(organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(of=AgentDecision)
    return query.all()


def lock_role_membership(
    db: Session,
    *,
    decision_id: int,
    organization_id: int,
) -> int | None:
    """Return one decision's current role, or ``None`` when it is unavailable."""
    rows = lock_role_memberships(
        db,
        decision_ids=(decision_id,),
        organization_id=organization_id,
    )
    if not rows or rows[0][1] is None:
        return None
    return int(rows[0][1])


def authorize_then_lock_role_membership(
    db: Session,
    *,
    decision_id: int,
    organization_id: int,
    authorize: Callable[[int], None],
) -> int | None:
    """Lock Role authority before Decision, rejecting membership drift."""
    row = (
        db.query(AgentDecision.role_id)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if row is None or row[0] is None:
        return None
    observed_role_id = int(row[0])
    authorize(observed_role_id)
    locked_role_id = lock_role_membership(
        db,
        decision_id=decision_id,
        organization_id=organization_id,
    )
    return observed_role_id if locked_role_id == observed_role_id else None


def lock_resolution_roles(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int],
) -> dict[int, Role]:
    """Lock all owner/acting Roles in global order before application rows."""
    locked: dict[int, Role] = {}
    for role_id in sorted({int(value) for value in role_ids}):
        role = role_query_for_update(
            db,
            role_id=role_id,
            organization_id=organization_id,
        ).populate_existing().one_or_none()
        if role is not None:
            locked[role_id] = role
    return locked


__all__ = [
    "apply_live_logical_decision_scope",
    "authorize_then_lock_role_membership",
    "LiveLogicalDecisionScope",
    "live_logical_decision_query",
    "lock_resolution_roles",
    "lock_role_membership",
    "lock_role_memberships",
    "resolve_live_logical_decision_scope",
]
