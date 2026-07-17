"""Bullhorn write-back for the automated (auto-reject / CV-gap / pre-screen) paths.

Extracted from ``application_automation_service`` so that file stays under the
architectural file-size gate. Mirrors ``actions.reject_application._try_bullhorn_reject``
for the automated paths, which were added on main after the Bullhorn branch's base
and so had no Bullhorn hook: a Bullhorn org would apply the reject locally but never
write it back.
"""
from __future__ import annotations

import logging

from typing import Any, Literal

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role

logger = logging.getLogger("taali.application_automation")


BULLHORN_REJECT_NOT_APPLICABLE = "not_applicable"
BULLHORN_REJECT_SUCCEEDED = "succeeded"
BULLHORN_REJECT_FAILED = "failed"
BullhornRejectOutcome = Literal["not_applicable", "succeeded", "failed"]


def bullhorn_reject_outcome(
    db,
    *,
    app: CandidateApplication,
    org: Organization | None,
    role: Role | None,
    actor_type: str,
    actor_id: int | None,
    reason: str | None,
    trigger: str,
) -> BullhornRejectOutcome:
    """Reject via the Bullhorn provider when the org routes to Bullhorn.

    The explicit tri-state prevents a Bullhorn-owned failure from being
    mistaken for "not Bullhorn" and falling through to a local-only reject.
    Honours strict mode identically: the provider raises ``WorkableWritebackError``
    on failure so the decision batch can re-queue; that propagates (never
    swallowed). Mirrors ``actions.reject_application._try_bullhorn_reject``.
    """
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from .workable_actions_service import WorkableWritebackError

    provider = resolve_application_ats_provider(org, db, app)
    if not isinstance(provider, BullhornProvider):
        return BULLHORN_REJECT_NOT_APPLICABLE
    if not (getattr(app, "bullhorn_job_submission_id", "") or "").strip():
        # Bullhorn org but this application isn't linked — nothing to write
        # upstream; the local reject stands. Handled.
        return BULLHORN_REJECT_SUCCEEDED
    try:
        result = provider.reject_application(app=app, role=role, reason=reason)
    except WorkableWritebackError:
        raise  # strict (batch) path — propagate so the batch re-queues.
    except Exception as exc:  # pragma: no cover — defensive
        logger.error(
            "bullhorn auto-reject raised unexpectedly application_id=%s error_type=%s",
            app.id,
            type(exc).__name__,
        )
        # Unknown provider outcome is never success.  Returning True here used
        # to flip the local outcome to rejected even when Bullhorn failed,
        # producing split-brain ATS state.
        try:
            append_application_event(
                db,
                app=app,
                event_type="bullhorn_writeback_failed",
                actor_type=actor_type,
                actor_id=actor_id,
                reason="Bullhorn reject raised unexpectedly",
                metadata={
                    "code": "unexpected_error",
                    "error_type": type(exc).__name__,
                    "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                    "trigger": trigger,
                },
            )
        except Exception as record_exc:
            logger.error(
                "failed to record Bullhorn write-back exception error_type=%s",
                type(record_exc).__name__,
            )
        return BULLHORN_REJECT_FAILED
    if result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_rejected",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or result.get("message") or "Rejected in Bullhorn",
            metadata={
                "code": result.get("code"),
                "bullhorn_status": result.get("config", {}).get("remote_status"),
                "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                "trigger": trigger,
            },
        )
    else:
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_writeback_failed",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=result.get("message") or "Bullhorn reject failed",
            metadata={
                "code": result.get("code"),
                "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                "trigger": trigger,
            },
        )
        logger.warning(
            "bullhorn auto-reject failed application_id=%s code=%s message=%s",
            app.id,
            result.get("code"),
            result.get("message"),
        )
        # NOT handled: a failed write-back (needs_mapping / api_error) must not be
        # treated as success. Return False so the caller leaves the local outcome
        # unflipped (no ``bullhorn_written`` marker) and runs its existing
        # fallback — mirroring the Workable write-back-failure behaviour.
        return BULLHORN_REJECT_FAILED
    return BULLHORN_REJECT_SUCCEEDED


def try_bullhorn_reject(
    db,
    *,
    app: CandidateApplication,
    org: Organization | None,
    role: Role | None,
    actor_type: str,
    actor_id: int | None,
    reason: str | None,
    trigger: str,
) -> bool:
    """Compatibility boolean for existing auto-reject callers."""

    return bullhorn_reject_outcome(
        db,
        app=app,
        org=org,
        role=role,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        trigger=trigger,
    ) == BULLHORN_REJECT_SUCCEEDED


def finalize_pre_screen_bullhorn_reject(
    db,
    *,
    app: CandidateApplication,
    org: Organization | None,
    role: Role | None,
    actor_type: str,
    actor_id: int | None,
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    """Pre-screen path: reject via Bullhorn before Workable linkage gates.

    Returns ``None`` only when Bullhorn does not own this application. A
    Bullhorn-owned failure returns an explicit failed result so the caller can
    surface Decision Hub review without trying Workable or rejecting locally.

    ``mark_auto_reject_state`` is passed in to avoid a circular import back into
    the pre_screening_service graph the caller already owns.
    """
    from .pre_screening_service import mark_auto_reject_state

    reason = decision.get("reason")
    outcome = bullhorn_reject_outcome(
        db,
        app=app,
        org=org,
        role=role,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        trigger="auto_reject_pre_screen",
    )
    if outcome == BULLHORN_REJECT_NOT_APPLICABLE:
        return None
    if outcome == BULLHORN_REJECT_FAILED:
        return {
            **decision,
            "performed": False,
            "state": "failed",
            "reason": "Bullhorn did not accept the auto-reject write-back",
            "bullhorn_written": False,
            "bullhorn_writeback_failed": True,
        }
    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Auto-rejected from pre-screen (Bullhorn)",
    )
    append_application_event(
        db,
        app=app,
        event_type="auto_rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata={
            "pre_screen_score": decision.get("snapshot", {}).get("pre_screen_score"),
            "threshold_100": (decision.get("config") or {}).get("threshold_100"),
            "bullhorn_written": True,
        },
    )
    mark_auto_reject_state(app, state="rejected", reason=reason, triggered=True)
    return {**decision, "performed": True, "state": "rejected", "bullhorn_written": True}
