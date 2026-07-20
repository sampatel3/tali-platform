"""Generation snapshots and commit fences for role-level provider artifacts.

Role prompts are assembled from a role row plus recruiter-authored criteria.
Those inputs can change while a slow provider request is in flight.  This
module reads the criteria table directly (never an ORM relationship cache) and
linearizes the eventual write in the platform's Organization -> Role lock
order so an old response cannot become the cache for a newer role generation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion


@dataclass(frozen=True)
class RoleProviderGeneration:
    role_id: int
    organization_id: int
    job_spec_text: str
    recruiter_requirements: str
    recruiter_criteria: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True)
class RoleProviderGenerationFence:
    role: Role | None
    reason: str | None = None
    detail: str | None = None

    @property
    def current(self) -> bool:
        return self.role is not None and self.reason is None


def capture_role_provider_generation(
    db: Session,
    *,
    role_id: int,
    organization_id: int | None = None,
) -> RoleProviderGeneration | None:
    """Read the durable provider inputs without trusting cached relationships."""

    role_query = db.query(
        Role.id,
        Role.organization_id,
        Role.job_spec_text,
    ).filter(
        Role.id == int(role_id),
        Role.deleted_at.is_(None),
    )
    if organization_id is not None:
        role_query = role_query.filter(
            Role.organization_id == int(organization_id)
        )
    with db.no_autoflush:
        role_row = role_query.one_or_none()
        if role_row is None:
            return None
        criteria = (
            db.query(
                RoleCriterion.bucket,
                RoleCriterion.text,
                RoleCriterion.must_have,
            )
            .filter(
                RoleCriterion.role_id == int(role_row.id),
                RoleCriterion.deleted_at.is_(None),
                RoleCriterion.source != CRITERION_SOURCE_DERIVED,
            )
            .order_by(RoleCriterion.ordering, RoleCriterion.id)
            .all()
        )

    # Keep formatting identical to every other role-intent prompt while the
    # underlying rows come from a fresh direct query.
    from .role_criteria_service import render_role_intent_items

    recruiter_requirements = render_role_intent_items(
        (row.bucket, row.text) for row in criteria
    )
    recruiter_criteria = tuple(
        (
            (row.text or "").strip(),
            row.bucket or "",
            bool(row.must_have),
        )
        for row in criteria
    )
    job_spec_text = (role_row.job_spec_text or "").strip()
    return RoleProviderGeneration(
        role_id=int(role_row.id),
        organization_id=int(role_row.organization_id),
        job_spec_text=job_spec_text,
        recruiter_requirements=recruiter_requirements,
        recruiter_criteria=recruiter_criteria,
    )


def lock_and_check_role_provider_generation(
    db: Session,
    *,
    expected: RoleProviderGeneration,
    requires_running_agent: bool,
) -> RoleProviderGenerationFence:
    """Lock current authority and verify that ``expected`` is still current."""

    from .role_execution_guard import (
        automatic_role_action_block_reason,
        lock_live_role,
    )
    from .workspace_agent_control import workspace_agent_is_paused

    role = lock_live_role(
        db,
        role_id=int(expected.role_id),
        organization_id=int(expected.organization_id),
    )
    if role is None:
        return RoleProviderGenerationFence(
            role=None,
            reason="role_unavailable",
            detail="role was deleted or moved",
        )
    if workspace_agent_is_paused(
        db, organization_id=int(expected.organization_id)
    ):
        return RoleProviderGenerationFence(
            role=role,
            reason="workspace_paused",
            detail="workspace agent is paused",
        )
    if requires_running_agent:
        block_reason = automatic_role_action_block_reason(role, db=db)
        if block_reason:
            return RoleProviderGenerationFence(
                role=role,
                reason="role_not_runnable",
                detail=block_reason,
            )

    current = capture_role_provider_generation(
        db,
        role_id=int(expected.role_id),
        organization_id=int(expected.organization_id),
    )
    if current is None:
        return RoleProviderGenerationFence(
            role=None,
            reason="role_unavailable",
            detail="role was deleted or moved",
        )
    if current != expected:
        return RoleProviderGenerationFence(
            role=role,
            reason="role_inputs_changed",
            detail="job specification or recruiter requirements changed",
        )
    return RoleProviderGenerationFence(role=role)


__all__ = [
    "RoleProviderGeneration",
    "RoleProviderGenerationFence",
    "capture_role_provider_generation",
    "lock_and_check_role_provider_generation",
]
