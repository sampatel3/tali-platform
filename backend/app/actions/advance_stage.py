"""Advance a candidate to a new pipeline stage.

Called by:
- Recruiter UI directly via ``PATCH /applications/{id}/stage``
- Agent decision approval via ``POST /agent-decisions/{id}/approve``

In both cases ``source="recruiter"`` because in v1 the agent never
directly transitions stages — the recruiter approves the agent's
queued recommendation. Approval metadata records ``agent_run_id`` and
``agent_decision_id`` for traceability.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from ..domains.assessments_runtime.role_support import get_application
from ..models.candidate_application import CandidateApplication
from .types import ACTOR_AGENT, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    to_stage: str,
    reason: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    expected_version: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> CandidateApplication:
    if actor.type == ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="Agent cannot directly advance stages — queue_advance_decision and let the recruiter approve.",
        )

    app = get_application(application_id, organization_id, db)
    application_lock = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(application_id),
        CandidateApplication.organization_id == int(organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        application_lock = application_lock.with_for_update()
    app = application_lock.populate_existing().one()
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="system",
        actor_id=actor.event_actor_id,
        reason="Pipeline initialized before stage change",
    )
    # Agent auto-approvals arrive as ``Actor.system`` (the LLM itself cannot
    # call this mutation directly) with an agent_decision_id in metadata. Keep
    # that provenance on the stage row/event instead of mislabelling the move
    # as recruiter-authored.
    stage_source = (
        "agent"
        if actor.type != "recruiter" and (metadata or {}).get("agent_decision_id")
        else "recruiter"
    )
    transition_stage(
        db,
        app=app,
        to_stage=to_stage,
        source=stage_source,
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Stage advanced",
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        metadata=metadata,
    )
    return app
