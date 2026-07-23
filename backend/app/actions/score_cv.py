"""Score a candidate's CV against the role.

Wraps the existing ``cv_score_orchestrator.enqueue_score``. The agent
calls this directly when it wants a fresh score before forming a queued
recommendation; the recruiter UI calls it via the existing batch-score
endpoint.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.role_support import get_application
from ..models.cv_score_job import CvScoreJob
from ..models.role import Role
from ..services.cv_score_orchestrator import enqueue_score
from ..services.logical_role_application_authority import (
    authorize_logical_role_application,
)
from .types import ACTOR_AGENT, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    role_id: int | None = None,
    force: bool = False,
    bypass_pre_screen: bool = False,
) -> Optional[CvScoreJob]:
    if role_id is None:
        # Recruiter-facing legacy callers are already authorized by their route.
        # Autonomous callers always pass role_id and cross the stricter logical-
        # role boundary below.
        app = get_application(application_id, organization_id, db)
    else:
        role = (
            db.query(Role)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == int(organization_id),
                Role.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if role is None:
            raise ValueError(f"role {role_id} not found")
        context = authorize_logical_role_application(
            db,
            role=role,
            application_id=int(application_id),
        )
        if context.is_related:
            raise ValueError(
                "Related-role scoring must use the role-local evaluation lifecycle"
            )
        app = context.source_application
    return enqueue_score(
        db,
        app,
        force=force,
        bypass_pre_screen=bypass_pre_screen,
        requires_active_agent=actor.type == ACTOR_AGENT,
    )
