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
from ..services.cv_score_orchestrator import enqueue_score
from .types import ACTOR_AGENT, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    force: bool = False,
    bypass_pre_screen: bool = False,
) -> Optional[CvScoreJob]:
    app = get_application(application_id, organization_id, db)
    return enqueue_score(
        db,
        app,
        force=force,
        bypass_pre_screen=bypass_pre_screen,
        requires_active_agent=actor.type == ACTOR_AGENT,
    )
