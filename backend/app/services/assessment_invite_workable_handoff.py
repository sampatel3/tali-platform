"""Generation-fenced Workable stage/note outbox for confirmed invites."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..domains.integrations_notifications.adapters import build_workable_adapter
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..platform.config import settings
from .workable_actions_service import (
    move_candidate_in_workable,
    resolve_workable_actor_member_id,
    workable_writeback_enabled,
)

HANDOFF_PENDING = "pending"
HANDOFF_RUNNING = "running"
HANDOFF_RETRY_WAIT = "retry_wait"
HANDOFF_SUCCEEDED = "succeeded"
HANDOFF_FAILED = "failed"
HANDOFF_SKIPPED = "skipped"

_HANDOFF_LEASE_SECONDS = 10 * 60
_HANDOFF_RETRY_CAP_SECONDS = 60 * 60


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
    row.invite_workable_handoff_last_error = str(
        error or "Workable handoff failed"
    )[:4000]
    row.invite_channel = "workable_partial"
    if terminal:
        row.invite_workable_handoff_status = HANDOFF_FAILED
        row.invite_workable_handoff_next_attempt_at = None
        if row.application_id:
            app = db.query(CandidateApplication).filter(
                CandidateApplication.id == int(row.application_id)
            ).one_or_none()
            if app is not None:
                append_application_event(
                    db,
                    app=app,
                    event_type="assessment_invite_workable_handoff_failed",
                    actor_type="system",
                    reason="Assessment email sent, but Workable handoff needs attention",
                    metadata={
                        "assessment_id": int(row.id),
                        "send_generation": generation,
                        "error": row.invite_workable_handoff_last_error,
                    },
                    idempotency_key=(
                        f"assessment-invite-workable-failed:{row.id}:{generation}"
                    ),
                )
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
    row = db.query(Assessment).filter(Assessment.id == int(assessment_id)).one_or_none()
    if row is None or _stored_generation(row.invite_workable_handoff_generation) != int(
        generation
    ):
        return None
    return int(row.organization_id)


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


def _skip_disabled(
    db: Session, *, assessment_id: int, generation: int
) -> dict:
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


def run_assessment_invite_workable_handoff(
    db: Session,
    *,
    assessment_id: int,
    generation: int,
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
    if status == HANDOFF_RUNNING and claimed_at and claimed_at > (
        now - timedelta(seconds=_HANDOFF_LEASE_SECONDS)
    ):
        return {"status": "in_flight"}

    row.invite_workable_handoff_status = HANDOFF_RUNNING
    row.invite_workable_handoff_claimed_at = now
    row.invite_workable_handoff_next_attempt_at = None
    db.commit()

    # Each successful external step is checkpointed before the next. Workable's
    # comment API has no idempotency key, so a process death after comment
    # acceptance but before the commit can still duplicate that note; the lease
    # and stable generation marker narrow this unavoidable gap.
    row = _load_handoff(db, int(assessment_id))
    if _stored_generation(row.invite_workable_handoff_generation) != int(generation):
        return {"status": "superseded"}
    org = row.organization
    stage = str(row.invite_workable_handoff_stage or "").strip()
    if settings.MVP_DISABLE_WORKABLE or org is None or not workable_writeback_enabled(org):
        return _skip_disabled(
            db, assessment_id=int(assessment_id), generation=int(generation)
        )
    if not row.workable_candidate_id or not stage:
        return _record_handoff_failure(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
            error="Workable candidate or invite stage is missing",
            terminal=True,
        )

    if row.invite_workable_stage_moved_at is None:
        try:
            move_result = move_candidate_in_workable(
                org=org,
                candidate_id=str(row.workable_candidate_id),
                target_stage=stage,
                role=row.role,
            )
        except Exception as exc:
            return _record_handoff_failure(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=str(exc),
                terminal=False,
            )
        if not move_result.get("success"):
            code = str(move_result.get("code") or "api_error")
            if move_result.get("skipped") and code == "writeback_disabled":
                fresh = _fresh_generation_row(
                    db, assessment_id=int(assessment_id), generation=int(generation)
                )
                return (
                    _skip_disabled(
                        db,
                        assessment_id=int(assessment_id),
                        generation=int(generation),
                    )
                    if fresh is not None
                    else {"status": "superseded"}
                )
            return _record_handoff_failure(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=str(move_result.get("message") or "Workable stage move failed"),
                terminal=(code != "api_error"),
            )
        fresh = _fresh_generation_row(
            db, assessment_id=int(assessment_id), generation=int(generation)
        )
        if fresh is None:
            db.rollback()
            return {"status": "superseded"}
        fresh.invite_workable_stage_moved_at = _now()
        if fresh.application is not None:
            fresh.application.workable_stage = stage
            fresh.application.workable_stage_local_write_at = _now()
        db.commit()
        row = _load_handoff(db, int(assessment_id))
        if _stored_generation(row.invite_workable_handoff_generation) != int(generation):
            return {"status": "superseded"}

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
    note = (
        "Taali assessment invite sent.\n\n"
        f"Candidate: {candidate_name} <{candidate_email}>\n"
        f"Assessment link: {link}\n"
        f"Delivery reference: {generation_key}\n"
    )
    member_id = resolve_workable_actor_member_id(org, role=row.role)
    if not member_id:
        return _record_handoff_failure(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
            error="Workable actor member is not configured",
            terminal=True,
        )
    try:
        result = build_workable_adapter(
            access_token=org.workable_access_token,
            subdomain=org.workable_subdomain,
        ).post_candidate_comment(
            str(row.workable_candidate_id), str(member_id), note
        )
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    if not result.get("success"):
        return _record_handoff_failure(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
            error=str(result.get("error") or "Workable note post failed"),
            terminal=False,
        )

    fresh = _fresh_generation_row(
        db, assessment_id=int(assessment_id), generation=int(generation)
    )
    if fresh is None:
        db.rollback()
        return {"status": "superseded"}
    fresh.invite_workable_note_posted_at = _now()
    fresh.invite_workable_handoff_status = HANDOFF_SUCCEEDED
    fresh.invite_workable_handoff_claimed_at = None
    fresh.invite_workable_handoff_next_attempt_at = None
    fresh.invite_workable_handoff_last_error = None
    fresh.invite_channel = "workable_hybrid"
    db.commit()
    return {"status": HANDOFF_SUCCEEDED, "assessment_id": int(fresh.id)}


__all__ = [
    "HANDOFF_FAILED",
    "HANDOFF_PENDING",
    "HANDOFF_RETRY_WAIT",
    "HANDOFF_RUNNING",
    "HANDOFF_SKIPPED",
    "HANDOFF_SUCCEEDED",
    "assessment_invite_workable_handoff_org",
    "defer_assessment_invite_workable_handoff",
    "run_assessment_invite_workable_handoff",
]
