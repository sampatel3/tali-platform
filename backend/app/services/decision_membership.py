"""Locked reads of an agent decision's current role membership."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.role import Role
from .role_concurrency import role_query_for_update


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
