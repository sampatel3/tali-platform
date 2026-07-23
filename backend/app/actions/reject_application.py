"""Reject a candidate application.

Sets ``application_outcome="rejected"`` via ``transition_outcome``. Called
by:
- Recruiter UI when they reject directly (via the ``PATCH /applications/
  {id}/outcome`` route, which has its own Workable sync)
- Agent decision approval (``POST /agent-decisions/{id}/approve``) when
  the agent's queued ``reject`` or ``skip_assessment_reject`` decision is
  approved by a recruiter

The agent itself never calls this — it queues a decision instead.

Notification policy — Taali NEVER emails candidates about the job.

All candidate job communication (including rejections) belongs to the ATS.
When a candidate is rejected, Taali disqualifies them in Workable
(``disqualify_candidate_in_workable``) and Workable's own disqualify-stage
workflow is what notifies the candidate. Taali only ever emails candidates
about the assessment itself (invite / expiry reminder / feedback) — never
about a hiring decision.

When Workable can't be written (req archived/closed, app not linked, org
disconnected, or the call fails), the candidate is rejected locally in Taali
and simply NOT emailed — the recruiter owns any candidate-facing message via
the ATS. We never send a Taali-branded rejection email.

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
from ..models.role import Role
from ..platform.config import settings
from ..services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
    authorize_logical_role_action_application,
)
from .types import ACTOR_AGENT, Actor


logger = logging.getLogger("taali.actions.reject_application")


def _try_workable_disqualify(
    db: Session,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    actor: Actor,
    reason: Optional[str],
    event_metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Attempt to disqualify the candidate in Workable.

    Taali never emails the candidate, so the return value is purely
    informational (it no longer gates a fallback email):
    - ``"handled"`` — disqualify succeeded; Workable's disqualify-stage
      workflow notifies the candidate.
    - ``"retry_scheduled"`` — the call failed with a transient API error
      (e.g. a 429 rate limit) and a bounded background retry was enqueued
      to push the disqualify through.
    - ``"already_disqualified"`` — Workable has already confirmed the same
      state, so there is no new movement or message to send.
    - ``"fallback"`` — Workable isn't applicable (not linked/configured) or
      the failure isn't retriable; the local reject stands and the candidate
      is not emailed by Taali.

    Records a ``workable_disqualified`` or ``workable_writeback_failed``
    application event mirroring the pre-screen auto-reject path so the
    audit trail is consistent.
    """
    if bool(getattr(app, "workable_disqualified", False)):
        return "already_disqualified"

    workable_candidate_id = (getattr(app, "workable_candidate_id", "") or "").strip()
    if not workable_candidate_id:
        return "fallback"
    if org is None:
        return "fallback"
    if not (
        getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    ):
        return "fallback"
    if settings.MVP_DISABLE_WORKABLE:
        return "fallback"

    from ..services.workable_actions_service import (
        WorkableWritebackError,
        disqualify_candidate_in_workable,
        workable_job_state,
        workable_job_syncable,
    )

    if not workable_job_syncable(getattr(app, "role", None)):
        # Archived/closed/draft Workable req — Workable 403s any disqualify
        # there. Skip the sync entirely; the local reject (transition_outcome in
        # run()) stands so the candidate resolves to 'rejected' instead of
        # waiting forever. Taali sends no candidate email — job comms are the
        # ATS's responsibility and the req is no longer live there.
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_skipped",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason="Workable req not live (archived/closed) — rejected in Taali only",
            metadata={
                **(event_metadata or {}),
                "action": "disqualify",
                "source": "reject_application",
                "workable_job_state": workable_job_state(getattr(app, "role", None)),
            },
        )
        return "fallback"

    try:
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=app.role,
            # ``None`` is intentional for Decision Hub resolutions: the
            # canonical movement summary is posted only after Workable confirms
            # the disqualification, and must be the sole activity-feed message.
            # Direct/manual rejection paths pass their explicit reason here.
            reason=reason,
            withdrew=False,
        )
    except WorkableWritebackError:
        # strict mode (decision-dispatch path): let the failure propagate so the
        # dispatch task can abort + re-queue. Never swallowed here.
        raise
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable disqualify raised unexpectedly (application_id=%s)", app.id
        )
        return "fallback"

    if result.get("skipped"):
        # Read-only mode: the disqualify is a benign no-op. The local reject
        # (transition_outcome in run()) stands and Taali sends no candidate
        # email — job comms belong to the ATS. Don't log a failure or schedule
        # a retry.
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_skipped",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason="read-only mode — rejected in Taali only",
            metadata={
                **(event_metadata or {}),
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": workable_candidate_id,
                "source": "reject_application",
            },
        )
        return "fallback"

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
                **(event_metadata or {}),
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": workable_candidate_id,
                "workable_actor_member_id": config.get("actor_member_id"),
                "workable_disqualify_reason_id": config.get("workable_disqualify_reason_id"),
                "source": "reject_application",
            },
        )
        return "handled"

    # Failure: log + record event.
    append_application_event(
        db,
        app=app,
        event_type="workable_writeback_failed",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=result.get("message") or "Workable disqualify failed",
        metadata={
            **(event_metadata or {}),
            "action": result.get("action"),
            "code": result.get("code"),
            "workable_candidate_id": workable_candidate_id,
            "source": "reject_application",
        },
    )
    logger.warning(
        "workable disqualify failed for application_id=%s code=%s message=%s",
        app.id,
        result.get("code"),
        result.get("message"),
    )
    # A transient API error (notably a 429 rate limit) shouldn't leave Taali
    # 'rejected' while Workable still shows the candidate active. Enqueue a
    # bounded, backed-off retry that pushes the disqualify through. Non-API
    # failures (bad config, unlinked candidate) won't self-heal, so record the
    # failure and stop; Taali never sends candidate job communications.
    if result.get("code") == "api_error":
        try:
            from ..tasks.workable_tasks import retry_workable_disqualify_task

            retry_workable_disqualify_task.apply_async(
                kwargs={"application_id": int(app.id), "reason": reason},
                countdown=60,
            )
            return "retry_scheduled"
        except Exception:  # pragma: no cover — best-effort enqueue
            logger.exception(
                "failed to enqueue workable disqualify retry application_id=%s", app.id
            )
    return "fallback"


def _try_bullhorn_reject(
    db: Session,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    actor: Actor,
    reason: Optional[str],
    event_metadata: Optional[dict[str, Any]] = None,
) -> Optional[bool]:
    """Reject via the Bullhorn provider when the org routes to Bullhorn.

    Returns ``None`` when the org does not route to Bullhorn, ``True`` when
    Bullhorn confirmed the write, and ``False`` when Bullhorn owns the route but
    the write was unlinked or failed. Records a ``bullhorn_rejected`` /
    ``bullhorn_writeback_failed`` event, mirroring the Workable trail. Honours
    strict mode: the provider raises ``WorkableWritebackError`` on failure so the
    decision-batch can re-queue; that propagates (never swallowed), exactly like
    the Workable disqualify.
    """
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from ..services.workable_actions_service import WorkableWritebackError

    provider = resolve_application_ats_provider(org, db, app)
    if not isinstance(provider, BullhornProvider):
        return None
    if not (getattr(app, "bullhorn_job_submission_id", "") or "").strip():
        # Bullhorn org but this application isn't linked — nothing was written.
        # Return False so callers never treat the local-only reject as a
        # confirmed ATS movement.
        return False
    try:
        result = provider.reject_application(app=app, role=getattr(app, "role", None), reason=reason)
    except WorkableWritebackError:
        raise  # strict (batch) path — propagate so the batch re-queues.
    except Exception as exc:  # pragma: no cover — defensive/provider boundary
        error_type = type(exc).__name__
        logger.error(
            "bullhorn reject raised unexpectedly application_id=%s error_type=%s",
            app.id,
            error_type,
        )
        # Recruiter-approved rejects run under the shared strict operation
        # shell.  Preserve its retry/requeue contract for unknown provider
        # failures instead of committing a Taali-only reject as success.
        raise WorkableWritebackError(
            action="reject",
            code="unexpected",
            message=f"Unexpected Bullhorn reject failure ({error_type})",
            retriable=True,
        ) from None
    if result.get("skipped") or result.get("code") == "already_at_target":
        # The external status is already rejected. The caller may reconcile
        # Taali's local outcome, but this is not a new movement and must not
        # produce a movement summary.
        return False
    if result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_rejected",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=reason or result.get("message") or "Rejected in Bullhorn",
            metadata={
                **(event_metadata or {}),
                "code": result.get("code"),
                "bullhorn_status": result.get("config", {}).get("remote_status"),
                "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                "source": "reject_application",
            },
        )
    else:
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=result.get("message") or "Bullhorn reject failed",
            metadata={
                **(event_metadata or {}),
                "code": result.get("code"),
                "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                "source": "reject_application",
            },
        )
        logger.warning(
            "bullhorn reject failed application_id=%s code=%s message=%s",
            app.id,
            result.get("code"),
            result.get("message"),
        )
        # A failed write-back must not be treated as a confirmed movement.
        return False
    return True


def notify_rejection(
    db: Session,
    *,
    app: CandidateApplication,
    actor: Actor,
    reason: Optional[str] = None,
    event_metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Resolve a rejection in the ATS — Taali never emails the candidate.

    Disqualifies the candidate in Workable when the org has write capability
    and the application is linked; Workable's own disqualify-stage workflow is
    what notifies the candidate. When Workable can't be written, the local
    reject stands and the candidate is not emailed by Taali (job comms belong
    to the ATS). Best-effort: never raises (except a strict
    ``WorkableWritebackError`` on the batch dispatch path). Extracted so the
    decision-resolution path can run the Workable HTTP call off the request
    thread via the deferred ``apply_decision_side_effects`` Celery task — it
    adds seconds the recruiter shouldn't wait on. Returns True only when the
    provider confirmed the rejection; unlinked, skipped, queued-for-retry and
    failed writes return False.
    """
    org = db.query(Organization).filter(Organization.id == app.organization_id).first()
    # Bullhorn-connected org → reject via the Bullhorn provider (writes the org's
    # rejected-category JobSubmission status). Same gating contract: under strict
    # mode a failure raises WorkableWritebackError so the decision-batch aborts +
    # re-queues, identical to the Workable path. A no-op for non-Bullhorn orgs.
    bullhorn_result = _try_bullhorn_reject(
        db,
        app=app,
        org=org,
        actor=actor,
        reason=reason,
        event_metadata=event_metadata,
    )
    if bullhorn_result is not None:
        return bullhorn_result
    # Otherwise disqualify in Workable so its disqualify-stage workflow notifies
    # the candidate. The local candidate row's email is irrelevant — Taali sends
    # no candidate email regardless; we only move the ATS status.
    result = _try_workable_disqualify(
        db,
        app=app,
        org=org,
        actor=actor,
        reason=reason,
        event_metadata=event_metadata,
    )
    return result == "handled"


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
    defer_notify: bool = False,
) -> CandidateApplication:
    if actor.type == ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="Agent cannot directly reject — queue_reject_decision and let the recruiter approve.",
        )

    app = get_application(
        application_id,
        organization_id,
        db,
        include_deleted=True,
    )
    acting_role_id = int((metadata or {}).get("acting_role_id") or app.role_id)
    acting_role = (
        db.query(Role)
        .filter(
            Role.id == acting_role_id,
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if acting_role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        context = authorize_logical_role_action_application(
            db,
            role=acting_role,
            application_id=int(application_id),
        )
    except LogicalRoleApplicationAuthorizationError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc
    app = context.source_application
    from ..services.related_role_action_service import (
        transition_related_role_outcome_action,
    )

    related_action = transition_related_role_outcome_action(
        db,
        application=app,
        acting_role_id=(metadata or {}).get("acting_role_id"),
        to_outcome="rejected",
        source=(
            "agent"
            if actor.type != "recruiter" and (metadata or {}).get("agent_decision_id")
            else "recruiter"
        ),
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Application rejected",
        metadata=metadata,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    if related_action is not None:
        return app
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

    # SessionLocal disables autoflush. Persist the transition inside the
    # caller-owned transaction before a later invocation refreshes this row
    # from the database; otherwise populate_existing() can restore the old
    # outcome and repeat the ATS side effect. This is deliberately a flush,
    # not a commit, so strict provider failures can still roll everything back.
    db.flush()

    # Resolve the rejection in the ATS, but only on a fresh rejection (not an
    # idempotent re-reject — transition_outcome is a no-op on the second call).
    # ``defer_notify`` lets the decision-resolution path skip the inline
    # Workable HTTP call and run it via a background task instead; the
    # caller computes the same freshness check and dispatches notify_rejection.
    notify = (
        not defer_notify
        and previous_outcome != "rejected"
        and app.application_outcome == "rejected"
    )
    if notify:
        notify_rejection(db, app=app, actor=actor, reason=reason)

    return app
