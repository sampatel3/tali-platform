"""Generation-fenced Workable stage/note outbox for confirmed invites."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..platform.config import settings

HANDOFF_PENDING = "pending"
HANDOFF_RUNNING = "running"
HANDOFF_RETRY_WAIT = "retry_wait"
HANDOFF_SUCCEEDED = "succeeded"
HANDOFF_FAILED = "failed"
HANDOFF_SKIPPED = "skipped"
_HANDOFF_LEASE_SECONDS = 10 * 60
_HANDOFF_RETRY_CAP_SECONDS = 60 * 60
_PROVIDER_ERROR_CODES = frozenset(
    {
        "api_error",
        "empty_body",
        "event_handler_failed",
        "initial_queue_unavailable",
        "missing_actor_member_id",
        "missing_candidate_id",
        "missing_connection",
        "missing_submission_id",
        "missing_target_stage",
        "missing_write_scope",
        "needs_mapping",
        "not_configured",
        "not_writeable",
        "provider_exception",
        "rate_limited",
        "skipped",
        "writeback_disabled",
    }
)

logger = logging.getLogger("taali.assessment_invite_workable_handoff")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _handoff_retry_delay(retry_count: int) -> int:
    exponent = min(max(retry_count - 1, 0), 6)
    return min(_HANDOFF_RETRY_CAP_SECONDS, 60 * (2**exponent))


def _stored_generation(value: int | None) -> int:
    return -1 if value is None else int(value)


def _stable_provider_error(
    provider: str,
    operation: str,
    code: object = "provider_exception",
) -> str:
    safe_code = str(code or "provider_exception").strip().lower().replace("-", "_")
    if safe_code not in _PROVIDER_ERROR_CODES:
        safe_code = "provider_exception"
    return f"{provider}_{operation}_{safe_code}"


def _fresh_generation_row(
    db: Session, *, assessment_id: int, generation: int
) -> Assessment | None:
    row = (
        db.query(Assessment)
        .filter(Assessment.id == int(assessment_id))
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if row is None or _stored_generation(row.invite_workable_handoff_generation) != int(
        generation
    ):
        return None
    return row


def _record_handoff_failure(
    db: Session,
    *,
    assessment_id: int,
    generation: int,
    error: str,
    terminal: bool,
) -> dict:
    row = _fresh_generation_row(
        db, assessment_id=int(assessment_id), generation=int(generation)
    )
    if row is None:
        db.rollback()
        return {"status": "superseded"}
    retries = int(row.invite_workable_handoff_retry_count or 0) + 1
    row.invite_workable_handoff_retry_count = retries
    row.invite_workable_handoff_claimed_at = None
    intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    provider_name = str(intent.get("ats_handoff_provider") or "workable").lower()
    provider_label = "Bullhorn" if provider_name == "bullhorn" else "Workable"
    row.invite_workable_handoff_last_error = str(
        error or f"{provider_label} handoff failed"
    )[:4000]
    row.invite_channel = f"{provider_name}_partial"
    if terminal:
        row.invite_workable_handoff_status = HANDOFF_FAILED
        row.invite_workable_handoff_next_attempt_at = None
        if row.application_id:
            app = (
                db.query(CandidateApplication)
                .filter(CandidateApplication.id == int(row.application_id))
                .one_or_none()
            )
            if app is not None:
                append_application_event(
                    db,
                    app=app,
                    event_type=f"assessment_invite_{provider_name}_handoff_failed",
                    actor_type="system",
                    reason=(
                        f"Assessment email sent, but {provider_label} handoff "
                        "needs attention"
                    ),
                    metadata={
                        "assessment_id": int(row.id),
                        "send_generation": generation,
                        "error": row.invite_workable_handoff_last_error,
                        "ats": provider_name,
                    },
                    idempotency_key=(
                        f"assessment-invite-{provider_name}-failed:{row.id}:{generation}"
                    ),
                )
                if (
                    "needs_mapping" in row.invite_workable_handoff_last_error
                    and row.role_id is not None
                ):
                    # A confirmed candidate email is never rolled back. Surface
                    # the missing remote status as explicit HITL so the ATS
                    # divergence cannot disappear inside an outbox error field.
                    try:
                        from ..actions import ask_recruiter
                        from ..actions.types import Actor

                        with db.begin_nested():
                            ask_recruiter.open(
                                db,
                                Actor.system(),
                                organization_id=int(row.organization_id),
                                role_id=int(row.role_id),
                                kind="other",
                                subject_id=int(app.id),
                                prompt=(
                                    "The assessment email was sent, but "
                                    f"'{row.role.name if row.role else 'this role'}' "
                                    f"has no unique {provider_label} assessment/"
                                    "invited stage mapped. Map it in Settings → "
                                    f"Integrations → {provider_label}; the agent "
                                    "did not guess or overwrite the candidate's "
                                    f"{provider_label} stage."
                                ),
                                rationale=row.invite_workable_handoff_last_error,
                                response_schema={
                                    "link_url": "/settings?tab=integrations",
                                    "link_label": (
                                        f"Open {provider_label} stage mapping"
                                    ),
                                },
                            )
                    except Exception:
                        # The durable assessment failure + application event are
                        # authoritative; a secondary Hub-card failure must not
                        # prevent them from committing.
                        pass
    else:
        row.invite_workable_handoff_status = HANDOFF_RETRY_WAIT
        row.invite_workable_handoff_next_attempt_at = _now() + timedelta(
            seconds=_handoff_retry_delay(retries)
        )
    db.commit()
    return {
        "status": HANDOFF_FAILED if terminal else HANDOFF_RETRY_WAIT,
        "retry_count": retries,
    }


def defer_assessment_invite_workable_handoff(
    db: Session,
    *,
    assessment_id: int,
    generation: int,
    error: str,
) -> dict:
    return _record_handoff_failure(
        db,
        assessment_id=int(assessment_id),
        generation=int(generation),
        error=error,
        terminal=False,
    )


def assessment_invite_workable_handoff_org(
    db: Session, *, assessment_id: int, generation: int
) -> int | None:
    context = assessment_invite_workable_handoff_context(
        db, assessment_id=assessment_id, generation=generation
    )
    return context[0] if context is not None else None


def assessment_invite_workable_handoff_context(
    db: Session, *, assessment_id: int, generation: int
) -> tuple[int, str] | None:
    """Return frozen ``(organization_id, provider)`` for mutex selection."""
    row = db.query(Assessment).filter(Assessment.id == int(assessment_id)).one_or_none()
    if row is None or _stored_generation(row.invite_workable_handoff_generation) != int(
        generation
    ):
        return None
    intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    provider = str(intent.get("ats_handoff_provider") or "workable").lower()
    return int(row.organization_id), (
        "bullhorn" if provider == "bullhorn" else "workable"
    )


def _load_handoff(db: Session, assessment_id: int) -> Assessment:
    return (
        db.query(Assessment)
        .options(
            joinedload(Assessment.organization),
            joinedload(Assessment.candidate),
            joinedload(Assessment.role),
            joinedload(Assessment.application),
        )
        .filter(Assessment.id == int(assessment_id))
        .populate_existing()
        .one()
    )


def _skip_disabled(db: Session, *, assessment_id: int, generation: int) -> dict:
    row = _fresh_generation_row(
        db, assessment_id=int(assessment_id), generation=int(generation)
    )
    if row is None:
        db.rollback()
        return {"status": "superseded"}
    row.invite_workable_handoff_status = HANDOFF_SKIPPED
    row.invite_workable_handoff_claimed_at = None
    row.invite_workable_handoff_last_error = (
        "Workable writeback was disabled before handoff"
    )
    if row.invite_channel != "workable_marketplace":
        row.invite_channel = "manual"
    db.commit()
    return {"status": HANDOFF_SKIPPED}


def _assessment_handoff_note(row: Assessment, generation: int) -> str:
    candidate = row.candidate
    candidate_email = str(getattr(candidate, "email", None) or "").strip()
    candidate_name = str(
        getattr(candidate, "full_name", None) or candidate_email or "Candidate"
    )
    generation_key = (
        f"assessment-invite/{row.id}"
        if int(generation) == 0
        else f"assessment-invite/{row.id}/resend/{int(generation)}"
    )
    link = f"{settings.FRONTEND_URL}/assessment/{row.id}?token={row.token}"
    return (
        "Taali assessment invite sent.\n\n"
        f"Candidate: {candidate_name} <{candidate_email}>\n"
        f"Assessment link: {link}\n"
        f"Delivery reference: {generation_key}\n"
    )


def run_assessment_invite_workable_handoff(
    db: Session,
    *,
    assessment_id: int,
    generation: int, should_yield: Callable[[], bool] | None = None,
) -> dict:
    """Run one leased Workable stage+note handoff without touching email."""
    now = _now()
    row = _fresh_generation_row(
        db, assessment_id=int(assessment_id), generation=int(generation)
    )
    if row is None:
        db.rollback()
        return {"status": "missing_or_superseded"}
    status = str(row.invite_workable_handoff_status or "")
    if status == HANDOFF_SUCCEEDED:
        return {"status": HANDOFF_SUCCEEDED, "deduplicated": True}
    if status in {HANDOFF_FAILED, HANDOFF_SKIPPED}:
        return {"status": status, "deduplicated": True}
    next_at = _aware(row.invite_workable_handoff_next_attempt_at)
    claimed_at = _aware(row.invite_workable_handoff_claimed_at)
    if status == HANDOFF_RETRY_WAIT and next_at is not None and next_at > now:
        return {"status": "not_due"}
    if (
        status == HANDOFF_RUNNING
        and claimed_at
        and claimed_at > (now - timedelta(seconds=_HANDOFF_LEASE_SECONDS))
    ):
        return {"status": "in_flight"}

    row.invite_workable_handoff_status = HANDOFF_RUNNING
    row.invite_workable_handoff_claimed_at = now
    row.invite_workable_handoff_next_attempt_at = None
    db.commit()

    # The shared ATS lifecycles persist exact per-generation provider receipts.
    row = _load_handoff(db, int(assessment_id))
    if _stored_generation(row.invite_workable_handoff_generation) != int(generation):
        return {"status": "superseded"}
    handoff_intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    provider = str(handoff_intent.get("ats_handoff_provider") or "workable").lower()
    from .assessment_invite_ats_handoff import run_assessment_invite_ats_handoff

    return run_assessment_invite_ats_handoff(
        db,
        row=row,
        generation=int(generation),
        provider=provider, should_yield=should_yield,
    )


__all__ = [
    "HANDOFF_FAILED",
    "HANDOFF_PENDING",
    "HANDOFF_RETRY_WAIT",
    "HANDOFF_RUNNING",
    "HANDOFF_SKIPPED",
    "HANDOFF_SUCCEEDED",
    "assessment_invite_workable_handoff_context",
    "assessment_invite_workable_handoff_org",
    "defer_assessment_invite_workable_handoff",
    "run_assessment_invite_workable_handoff",
]
