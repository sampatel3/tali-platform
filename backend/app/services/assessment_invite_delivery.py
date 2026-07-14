"""Durable provider confirmation and post-email Workable handoff.

Email delivery and ATS writeback are deliberately two state machines:

* Resend acceptance (with a provider message id) atomically confirms the
  invite and advances the local application to ``invited``.
* Only after that commit may a generation-scoped Workable handoff move the
  candidate and post its note.  Its lease/retry state lives on Assessment, so
  retrying Workable never submits the email again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    normalize_pipeline_key,
    transition_stage,
)
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from .assessment_invite_workable_handoff import (
    HANDOFF_PENDING,
    HANDOFF_RETRY_WAIT,
    HANDOFF_RUNNING,
    HANDOFF_SKIPPED,
)
_ADVANCED_EMAIL_STATUSES = {"delivered", "opened", "clicked", "bounced", "complained"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pipeline_intent(assessment: Assessment) -> dict:
    value = assessment.invite_pipeline_transition
    return dict(value) if isinstance(value, dict) else {}


def _append_pipeline_hold_event(
    db: Session,
    *,
    app: CandidateApplication,
    assessment: Assessment,
    generation: int,
    reason: str,
) -> None:
    append_application_event(
        db,
        app=app,
        event_type="assessment_invite_pipeline_transition_held",
        actor_type="system",
        reason=reason,
        metadata={
            "assessment_id": int(assessment.id),
            "send_generation": generation,
            "current_stage": app.pipeline_stage,
            "current_version": int(app.version or 0),
        },
        idempotency_key=f"assessment-invite-pipeline-held:{assessment.id}:{generation}",
    )


def _confirm_local_pipeline(
    db: Session,
    *,
    assessment: Assessment,
    generation: int,
    email_id: str,
) -> None:
    """Apply the frozen pipeline intent inside the provider-success txn."""
    if not assessment.application_id:
        return
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(assessment.application_id),
            CandidateApplication.organization_id == int(assessment.organization_id),
        )
        .with_for_update()
        .one_or_none()
    )
    if app is None:
        return

    intent = _pipeline_intent(assessment)
    actor_type = str(intent.get("actor_type") or "system")
    actor_id = intent.get("actor_id")
    source = str(intent.get("source") or "agent")
    expected_stage = normalize_pipeline_key(intent.get("expected_stage"))
    expected_version = intent.get("expected_version")
    event_type = str(intent.get("event_type") or "assessment_invite_sent")
    reason = str(intent.get("reason") or "Assessment invite accepted by email provider")
    metadata = dict(intent.get("metadata") or {})
    metadata.update(
        {
            "assessment_id": int(assessment.id),
            "send_generation": generation,
            "provider_email_id": email_id,
        }
    )

    ensure_pipeline_fields(app)
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="system",
        actor_id=int(actor_id) if actor_id is not None else None,
        reason="Pipeline initialized when assessment delivery was confirmed",
    )
    current_stage = normalize_pipeline_key(app.pipeline_stage)
    current_version = int(app.version or 0)
    expected_unchanged = (
        (not expected_stage or current_stage == expected_stage)
        and (expected_version is None or current_version == int(expected_version))
    )
    if normalize_pipeline_key(app.application_outcome) != "open":
        _append_pipeline_hold_event(
            db,
            app=app,
            assessment=assessment,
            generation=generation,
            reason="Invite delivered after the application had already closed; stage was preserved",
        )
    elif current_stage == "invited":
        pass
    elif not expected_unchanged:
        _append_pipeline_hold_event(
            db,
            app=app,
            assessment=assessment,
            generation=generation,
            reason="Invite delivered after a concurrent pipeline change; newer stage was preserved",
        )
    else:
        try:
            transition_stage(
                db,
                app=app,
                to_stage="invited",
                source=source,
                actor_type=actor_type,
                actor_id=int(actor_id) if actor_id is not None else None,
                reason=reason,
                metadata=metadata,
                idempotency_key=f"assessment-invite-stage:{assessment.id}:{generation}",
            )
        except HTTPException as exc:
            # A concurrent/reconfigured pipeline must never make provider
            # confirmation uncommittable.  Preserve it and surface a precise
            # audit event instead of retrying an already accepted email forever.
            _append_pipeline_hold_event(
                db,
                app=app,
                assessment=assessment,
                generation=generation,
                reason=f"Invite delivered but pipeline transition was held: {exc.detail}",
            )

    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=int(actor_id) if actor_id is not None else None,
        reason=reason,
        metadata=metadata,
        idempotency_key=f"assessment-invite-confirmed:{assessment.id}:{generation}",
    )


def confirm_assessment_invite_provider_success(
    db: Session,
    *,
    assessment_id: int,
    email_id: str,
    expected_generation: int,
) -> dict:
    """Commit truthful local invite state after Resend accepted the email."""
    row = (
        db.query(Assessment)
        .filter(Assessment.id == int(assessment_id))
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        return {"confirmed": False, "reason": "missing"}
    generation = int(row.invite_email_send_generation or 0)
    if generation != int(expected_generation):
        return {"confirmed": False, "reason": "superseded_generation"}
    if bool(row.is_voided):
        return {"confirmed": False, "reason": "voided"}
    provider_id = str(email_id or "").strip()
    if not provider_id:
        return {"confirmed": False, "reason": "missing_provider_id"}

    if (
        (
            -1
            if row.invite_email_confirmed_generation is None
            else int(row.invite_email_confirmed_generation)
        )
        == generation
        and row.invite_sent_at is not None
        and str(row.invite_email_id or "") == provider_id
    ):
        return {
            "confirmed": True,
            "deduplicated": True,
            "handoff_pending": row.invite_workable_handoff_status
            in {HANDOFF_PENDING, HANDOFF_RUNNING, HANDOFF_RETRY_WAIT},
            "organization_id": int(row.organization_id),
        }

    now = _now()
    row.invite_email_id = provider_id
    if str(row.invite_email_status or "") not in _ADVANCED_EMAIL_STATUSES:
        row.invite_email_status = "sent"
    row.invite_email_confirmed_generation = generation
    row.invite_email_retry_count = 0
    row.invite_email_next_attempt_at = None
    row.invite_email_claimed_at = None
    row.invite_email_last_error = None
    row.invite_sent_at = now
    _confirm_local_pipeline(
        db,
        assessment=row,
        generation=generation,
        email_id=provider_id,
    )

    stage = str(_pipeline_intent(row).get("workable_handoff_stage") or "").strip()
    if stage:
        row.invite_workable_handoff_stage = stage
        row.invite_workable_handoff_generation = generation
        row.invite_workable_handoff_status = HANDOFF_PENDING
        row.invite_workable_handoff_retry_count = 0
        row.invite_workable_handoff_next_attempt_at = None
        row.invite_workable_handoff_claimed_at = None
        row.invite_workable_handoff_last_error = None
        row.invite_workable_stage_moved_at = None
        row.invite_workable_note_posted_at = None
        row.invite_channel = "workable_pending"
        handoff_pending = True
    else:
        row.invite_workable_handoff_generation = generation
        row.invite_workable_handoff_stage = None
        row.invite_workable_handoff_status = HANDOFF_SKIPPED
        row.invite_workable_handoff_claimed_at = None
        row.invite_workable_handoff_next_attempt_at = None
        if row.invite_channel != "workable_marketplace":
            row.invite_channel = "manual"
        handoff_pending = False
    db.commit()
    return {
        "confirmed": True,
        "deduplicated": False,
        "handoff_pending": handoff_pending,
        "organization_id": int(row.organization_id),
    }


__all__ = [
    "confirm_assessment_invite_provider_success",
]
