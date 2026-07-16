"""Provider-first rejection for applications whose CV cannot be evaluated."""

from __future__ import annotations

from typing import Any

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .bullhorn_auto_reject import try_bullhorn_reject
from .document_service import sanitize_text_for_storage
from .workable_actions_service import disqualify_candidate_in_workable


def reject_for_cv_gap(
    *,
    db,
    org: Organization | None,
    app: CandidateApplication,
    role: Role | None,
    actor_type: str,
    actor_id: int | None = None,
    reason: str = "No CV on file",
    trigger: str = "reject_cv_gap",
    disqualify_fn=disqualify_candidate_in_workable,
) -> dict[str, Any]:
    """Reject after provider confirmation, or leave the local row open."""

    if try_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        trigger=trigger,
    ):
        ensure_pipeline_fields(app)
        transition_outcome(
            db,
            app=app,
            to_outcome="rejected",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return {"performed": True, "reason": reason, "bullhorn_written": True}

    workable_linked = bool(getattr(app, "workable_candidate_id", None))
    org_writeable = bool(
        org
        and getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )
    wrote_to_workable = workable_linked and org_writeable
    if wrote_to_workable:
        result = disqualify_fn(
            org=org,
            app=app,
            role=role,
            reason=reason,
            withdrew=False,
        )
        if not result.get("success"):
            msg = sanitize_text_for_storage(
                str(
                    result.get("message")
                    or "Failed to disqualify candidate in Workable"
                )
            ) or "Failed to disqualify candidate in Workable"
            append_application_event(
                db,
                app=app,
                event_type="workable_writeback_failed",
                actor_type=actor_type,
                actor_id=actor_id,
                reason=msg,
                metadata={
                    "action": result.get("action"),
                    "code": result.get("code"),
                    "trigger": trigger,
                    "workable_candidate_id": app.workable_candidate_id,
                },
            )
            return {
                "performed": False,
                "reason": msg,
                "workable_result": result,
            }

    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
    )
    if wrote_to_workable:
        append_application_event(
            db,
            app=app,
            event_type="workable_disqualified",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "trigger": trigger,
                "workable_candidate_id": app.workable_candidate_id,
            },
        )
    return {
        "performed": True,
        "reason": reason,
        "workable_written": wrote_to_workable,
    }


__all__ = ["reject_for_cv_gap"]
