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

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..domains.integrations_notifications.adapters import build_workable_adapter
from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..platform.config import settings
from .types import ACTOR_AGENT, Actor


logger = logging.getLogger("taali.actions.post_workable_note")

_MAX_NOTE_LENGTH = 8000  # Workable activity body limit is generous; cap to keep notes legible.


@dataclass(frozen=True)
class PostWorkableNoteResult:
    application_id: int
    status: str  # "posted" | "skipped" | "failed" | "blocked"
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

    app = None
    if actor.type == ACTOR_AGENT:
        # The runtime's role object and allowlist may become stale while an LLM
        # turn is in flight. Re-authorize this external write under the live
        # Organization -> Role lock, then lock the target application beneath
        # it so pause/off/delete/permission and membership changes cannot race
        # through the Workable call.
        with db.no_autoflush:
            role_id = (
                db.query(CandidateApplication.role_id)
                .filter(
                    CandidateApplication.id == int(application_id),
                    CandidateApplication.organization_id == int(organization_id),
                    CandidateApplication.deleted_at.is_(None),
                )
                .scalar()
            )
        if role_id is None:
            return PostWorkableNoteResult(
                application_id=application_id,
                status="blocked",
                detail="Automatic Workable note held: application is unavailable",
            )

        from ..services.role_execution_guard import (
            automatic_role_action_block_reason,
            lock_live_role,
        )

        live_role = lock_live_role(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
        block_reason = automatic_role_action_block_reason(live_role, db=db)
        if block_reason:
            return PostWorkableNoteResult(
                application_id=application_id,
                status="blocked",
                detail=f"Automatic Workable note held: {block_reason}",
            )
        configured_actions = getattr(live_role, "agent_action_allowlist", None)
        if not isinstance(configured_actions, (list, tuple, set, frozenset)) or (
            "post_workable_note"
            not in {str(name).strip() for name in configured_actions}
        ):
            return PostWorkableNoteResult(
                application_id=application_id,
                status="blocked",
                detail=(
                    "Automatic Workable note held: post_workable_note is no "
                    "longer enabled for this role"
                ),
            )
        app = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.role_id == int(live_role.id),
                CandidateApplication.deleted_at.is_(None),
            )
            .with_for_update(of=CandidateApplication)
            .populate_existing()
            .one_or_none()
        )
        if app is None:
            return PostWorkableNoteResult(
                application_id=application_id,
                status="blocked",
                detail=(
                    "Automatic Workable note held: application membership "
                    "changed before write-back"
                ),
            )
    else:
        app = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
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

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not (
        org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
    ):
        return PostWorkableNoteResult(
            application_id=application_id,
            status="skipped",
            detail="Workable is not connected for this organization",
        )

    from ..services.workable_actions_service import (
        resolve_workable_actor_member_id,
        workable_writeback_enabled,
    )

    # Read-only mode: Taali never writes to Workable, including agent/recruiter
    # notes and assessment-result posts. Skip locally (no error).
    if not workable_writeback_enabled(org):
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

    svc = build_workable_adapter(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = svc.post_candidate_comment(
        candidate_id=app.workable_candidate_id, member_id=member_id, body=note
    )
    if not result.get("success"):
        logger.warning(
            "workable note post failed application_id=%s detail=%s",
            application_id,
            result.get("error"),
        )
        return PostWorkableNoteResult(
            application_id=application_id,
            status="failed",
            detail=str(result.get("error") or "post_candidate_comment returned no success"),
        )

    ensure_pipeline_fields(app)
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="system",
        actor_id=actor.event_actor_id,
        reason="Pipeline initialized before Workable note post",
    )
    append_application_event(
        db,
        app=app,
        event_type="workable_note_posted",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason="Workable activity note posted",
        metadata={"body_preview": note[:240]},
    )
    return PostWorkableNoteResult(application_id=application_id, status="posted")
