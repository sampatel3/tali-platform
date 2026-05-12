"""Request public artifacts from a candidate — Amendment A2.

When the task-selection sub-agent decides ``request_artifacts``
(insufficient existing artifacts AND no calibrated template to send),
this action logs the request through the shared action layer so the
audit trail and idempotency guarantees are identical to every other
candidate-facing action.

The actual delivery channel (email / portal notification / Workable
note) is configured per org and dispatched elsewhere. This module
handles the side effect of *recording the request* — the canonical
write that downstream notification jobs read.

Idempotency key format follows the existing convention:
  ``{run_id}:{application_id}:request_artifacts``

Re-running with the same key returns the existing log row rather than
creating a duplicate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from .types import ACTOR_AGENT, ACTOR_RECRUITER, Actor


logger = logging.getLogger("taali.actions.request_candidate_artifacts")


EVENT_TYPE = "candidate_artifacts_requested"


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    dimensions: list[str],
    note: Optional[str] = None,
) -> CandidateApplicationEvent:
    """Log a request for the listed dimensions on the application.

    Returns the event row. Caller commits.

    Both ``ACTOR_AGENT`` (from the task_selection sub-agent) and
    ``ACTOR_RECRUITER`` (manual request) are allowed — this is the
    shared action layer.
    """
    if actor.type not in (ACTOR_AGENT, ACTOR_RECRUITER):
        raise HTTPException(
            status_code=403,
            detail="request_candidate_artifacts requires agent or recruiter actor",
        )
    if not dimensions:
        raise HTTPException(status_code=422, detail="dimensions cannot be empty")
    # Validate the application belongs to the org.
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .one_or_none()
    )
    if app is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not in org {organization_id}",
        )

    # Idempotency: agent caller carries an agent_run_id. Recruiter
    # caller falls back to user_id so manual triggers also dedupe
    # within a session.
    actor_id = actor.agent_run_id if actor.type == ACTOR_AGENT else actor.user_id
    idempotency_key = f"{actor_id}:{application_id}:request_artifacts"

    existing = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing is not None:
        return existing

    reason = f"dimensions={','.join(dimensions)}"
    if note:
        reason = f"{reason} | {note[:500]}"

    row = CandidateApplicationEvent(
        organization_id=organization_id,
        application_id=application_id,
        event_type=EVENT_TYPE,
        actor_type=actor.type,
        actor_id=int(actor_id) if actor_id else None,
        reason=reason,
        event_metadata={"dimensions": list(dimensions)},
        idempotency_key=idempotency_key,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


__all__ = ["EVENT_TYPE", "run"]
