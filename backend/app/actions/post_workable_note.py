"""Post a free-form note to a candidate's Workable activity feed.

Both the recruiter UI and the agent invoke this through the same
action. The agent uses it when it needs to leave a contextual note on
a candidate that doesn't correspond to a stage change (e.g. "I queued
a rejection because criteria X failed; recruiter should confirm").
The recruiter uses it when they want to log an off-system observation
(phone screen notes, side-channel info from a referrer, etc.) against
the Workable record.

The recruiter UI flow uses this exactly once today: posting completed
assessment results back to Workable. That call goes through
``post_assessment_to_workable`` which composes the body and then
delegates to this action.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..platform.config import settings
from .types import Actor


_MAX_NOTE_LENGTH = (
    8000  # Workable activity body limit is generous; cap to keep notes legible.
)


@dataclass(frozen=True)
class PostWorkableNoteResult:
    application_id: int
    status: str  # "queued" | "skipped" | "failed"
    detail: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "application_id": self.application_id,
            "status": self.status,
            "detail": self.detail,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    body: str,
) -> PostWorkableNoteResult:
    if settings.MVP_DISABLE_WORKABLE:
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Workable integration is disabled (MVP flag)",
        )

    note = (body or "").strip()
    if not note:
        raise HTTPException(status_code=422, detail="note body cannot be empty")
    if len(note) > _MAX_NOTE_LENGTH:
        note = note[:_MAX_NOTE_LENGTH]

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not app.workable_candidate_id:
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Application has no linked Workable candidate",
        )

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not (
        org.workable_connected and org.workable_access_token and org.workable_subdomain
    ):
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Workable is not connected for this organization",
        )

    from ..services.workable_actions_service import (
        resolve_workable_actor_member_id,
        workable_can_write_candidates,
        workable_writeback_enabled,
    )

    # Read-only mode: Taali never writes to Workable, including agent/recruiter
    # notes and assessment-result posts. Skip locally (no error).
    if not workable_writeback_enabled(org) or not workable_can_write_candidates(org):
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Workable write-back is off (read-only mode)",
        )

    member_id = resolve_workable_actor_member_id(org, getattr(app, "role", None))
    if not member_id:
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Workable actor member is not configured for this organization",
        )

    from ..services.workable_op_runner import OP_POST_NOTE, enqueue_workable_op

    actor_key = (
        f"{actor.type}:{int(actor.event_actor_id)}"
        if actor.event_actor_id is not None
        else f"{actor.type}:{uuid4().hex}"
    )
    dispatch_key = (
        f"legacy-workable-note:{organization_id}:{application_id}:{actor_key}:"
        f"{hashlib.sha256(note.encode('utf-8')).hexdigest()}"
    )[:200]
    try:
        job_run_id = enqueue_workable_op(
            organization_id=int(organization_id),
            op_type=OP_POST_NOTE,
            payload={
                "application_id": int(app.id),
                "body": note,
                "provider": "workable",
                "provider_target_id": str(app.workable_candidate_id),
                "candidate_provider_id": str(app.workable_candidate_id),
                "actor_type": actor.type,
                "actor_id": actor.event_actor_id,
            },
            scope_id=int(app.id),
            dispatch_key=dispatch_key,
        )
    except Exception:
        return PostWorkableNoteResult(
            application_id=application_id,
            status="failed",
            detail="ATS note could not be durably queued; no provider call was made",
        )
    return PostWorkableNoteResult(
        application_id=application_id,
        status="queued",
        detail=f"Durable ATS note job {int(job_run_id)} queued",
    )
