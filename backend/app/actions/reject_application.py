"""Reject a candidate application.

Sets ``application_outcome="rejected"`` via ``transition_outcome``. Called
by:
- Recruiter UI when they reject directly (via the ``PATCH /applications/
  {id}/outcome`` route, which has its own Workable sync)
- Agent decision approval (``POST /agent-decisions/{id}/approve``) when
  the agent's queued ``reject`` or ``skip_assessment_reject`` decision is
  approved by a recruiter

The agent itself never calls this — it queues a decision instead.

Notification policy (matches the existing pre-screen auto-reject and
manual recruiter-outcome PATCH paths):

1. If the application is linked to Workable AND the org has Workable
   write capability → call ``disqualify_candidate_in_workable``. On
   success, the org's Workable disqualify-stage workflow is responsible
   for sending the rejection email; we DO NOT send a Taali-branded one
   on top.
2. If Workable isn't connected, the application isn't linked, or the
   disqualify call fails → send the Taali rejection email as a fallback
   so the candidate still gets notified.

Workable failures are logged and recorded as application events but do
NOT raise — unlike the manual recruiter outcome PATCH which raises 502.
The agent-approved reject path shouldn't surface a Workable hiccup as a
hard failure to the recruiter who already clicked Approve.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    initialize_pipeline_event_if_missing,
    transition_outcome,
)
from ..domains.assessments_runtime.role_support import get_application
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..platform.config import settings
from .types import ACTOR_AGENT, Actor


logger = logging.getLogger("taali.actions.reject_application")


def _dispatch_rejection_email(
    *,
    candidate_email: str,
    candidate_name: str,
    org_name: str,
    position: str,
) -> None:
    """Enqueue the rejection email on Celery.

    Best-effort: any exception is logged and swallowed — the rejection
    has already landed in the DB before this fires.
    """
    if not (settings.RESEND_API_KEY or "").strip():
        return
    from ..components.notifications.tasks import send_application_rejected_email

    try:
        send_application_rejected_email.delay(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            org_name=org_name,
            position=position,
        )
    except Exception:  # pragma: no cover — best-effort
        logger.exception(
            "rejection email enqueue failed (candidate=%s)", candidate_email
        )


def _try_workable_disqualify(
    db: Session,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    actor: Actor,
    reason: Optional[str],
) -> bool:
    """Attempt to disqualify the candidate in Workable.

    Returns ``True`` if Workable handled the rejection (disqualify
    succeeded), in which case the caller should NOT send a Taali email
    — Workable's stage workflow does. Returns ``False`` when Workable
    isn't applicable (not linked, not configured) or the API failed; the
    caller should fall back to the Taali email.

    Records a ``workable_disqualified`` or ``workable_writeback_failed``
    application event mirroring the pre-screen auto-reject path so the
    audit trail is consistent.
    """
    workable_candidate_id = (getattr(app, "workable_candidate_id", "") or "").strip()
    if not workable_candidate_id:
        return False
    if org is None:
        return False
    if not (
        getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    ):
        return False
    if settings.MVP_DISABLE_WORKABLE:
        return False

    from ..services.workable_actions_service import disqualify_candidate_in_workable

    try:
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=app.role,
            reason=reason or "Rejected via Taali",
            withdrew=False,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable disqualify raised unexpectedly (application_id=%s)", app.id
        )
        return False

    if result.get("success"):
        config = result.get("config") or {}
        append_application_event(
            db,
            app=app,
            event_type="workable_disqualified",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=reason or result.get("message") or "Workable disqualified",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": workable_candidate_id,
                "workable_actor_member_id": config.get("actor_member_id"),
                "workable_disqualify_reason_id": config.get("workable_disqualify_reason_id"),
                "source": "reject_application",
            },
        )
        return True

    # Failure: log + record event, fall back to Taali email.
    append_application_event(
        db,
        app=app,
        event_type="workable_writeback_failed",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=result.get("message") or "Workable disqualify failed",
        metadata={
            "action": result.get("action"),
            "code": result.get("code"),
            "workable_candidate_id": workable_candidate_id,
            "source": "reject_application",
        },
    )
    logger.warning(
        "workable disqualify failed for application_id=%s code=%s message=%s; "
        "falling back to Taali rejection email",
        app.id,
        result.get("code"),
        result.get("message"),
    )
    return False


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    reason: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    expected_version: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    send_email: bool = True,
) -> CandidateApplication:
    if actor.type == ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="Agent cannot directly reject — queue_reject_decision and let the recruiter approve.",
        )

    app = get_application(application_id, organization_id, db)
    previous_outcome = app.application_outcome
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="system",
        actor_id=actor.event_actor_id,
        reason="Pipeline initialized before rejection",
    )
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Application rejected",
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        metadata=metadata,
    )

    # Notify the candidate, but only on a fresh rejection (not idempotent
    # re-reject — transition_outcome is a no-op on the second call).
    notify = (
        send_email
        and previous_outcome != "rejected"
        and app.application_outcome == "rejected"
    )
    if notify:
        candidate = app.candidate
        candidate_email = (getattr(candidate, "email", "") or "").strip() if candidate else ""
        org = (
            db.query(Organization).filter(Organization.id == organization_id).first()
        )
        # Workable-first: when the org has Workable connected and the
        # application is linked, disqualify there regardless of whether
        # the local candidate row has an email. ``Candidate.email`` is
        # nullable for imported / partially-populated records, but those
        # candidates still need their Workable status moved to rejected.
        # Only the fallback Taali-branded email requires a local email.
        workable_handled = _try_workable_disqualify(
            db,
            app=app,
            org=org,
            actor=actor,
            reason=reason,
        )
        if not workable_handled and candidate_email:
            role = app.role
            position = (
                getattr(role, "name", None)
                or getattr(candidate, "position", None)
                or "the role you applied for"
            )
            _dispatch_rejection_email(
                candidate_email=candidate_email,
                candidate_name=(candidate.full_name or candidate.email),
                org_name=(org.name if org else "the hiring team"),
                position=position,
            )

    return app
