"""Generation-fenced Workable stage/note outbox for confirmed invites."""

from __future__ import annotations

import logging
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
    resolve_workable_invite_stage,
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
    """Build a bounded receipt code without carrying provider response text."""

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
            app = db.query(CandidateApplication).filter(
                CandidateApplication.id == int(row.application_id)
            ).one_or_none()
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


def _run_bullhorn_assessment_handoff(
    db: Session,
    *,
    row: Assessment,
    generation: int,
) -> dict:
    """Run Bullhorn's confirmed-invite move+note through the shared op runner.

    The surrounding notification task already owns the same per-org serialized
    mutex as every ATS op. Calling ``execute_op`` here reuses provider routing,
    strict write errors, local-write stamping, and application audit events
    without enqueueing a second nested task or ever resending the email.
    """
    from .workable_actions_service import WorkableWritebackError
    from .workable_op_runner import (
        OP_MOVE_STAGE,
        OP_POST_NOTE,
        execute_op,
    )
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider

    app = row.application
    stage_intent = str(row.invite_workable_handoff_stage or "invited").strip()
    frozen_intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    actor_type = str(frozen_intent.get("actor_type") or "system")
    actor_id = frozen_intent.get("actor_id")
    source = str(frozen_intent.get("source") or actor_type)
    if (
        app is None
        or not app.bullhorn_job_submission_id
        or row.candidate is None
        or not getattr(row.candidate, "bullhorn_candidate_id", None)
    ):
        return _record_handoff_failure(
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error="Bullhorn application/candidate linkage is missing",
            terminal=True,
        )
    if not isinstance(
        resolve_application_ats_provider(row.organization, db, app), BullhornProvider
    ):
        return _record_handoff_failure(
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=(
                "bullhorn_unavailable: Bullhorn is disabled or disconnected; "
                "the assessment email remains confirmed locally"
            ),
            terminal=True,
        )

    if row.invite_workable_stage_moved_at is None:
        try:
            moved = execute_op(
                db,
                organization_id=int(row.organization_id),
                op_type=OP_MOVE_STAGE,
                payload={
                    "application_id": int(app.id),
                    "target_stage": stage_intent,
                    "target_intent": stage_intent,
                    "reason": "Confirmed assessment invite handed off to Bullhorn",
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "source": source,
                },
            )
        except WorkableWritebackError as exc:
            logger.exception(
                "Bullhorn invite stage handoff failed assessment_id=%s code=%s",
                row.id,
                exc.code,
            )
            return _record_handoff_failure(
                db,
                assessment_id=int(row.id),
                generation=int(generation),
                error=_stable_provider_error(
                    "bullhorn", "stage_move", exc.code
                ),
                terminal=not bool(exc.retriable),
            )
        except Exception as exc:
            logger.exception(
                "Bullhorn invite stage handoff raised assessment_id=%s error_type=%s",
                row.id,
                type(exc).__name__,
            )
            return _record_handoff_failure(
                db,
                assessment_id=int(row.id),
                generation=int(generation),
                error=_stable_provider_error("bullhorn", "stage_move"),
                terminal=False,
            )
        if moved.get("status") != "ok":
            logger.error(
                "Bullhorn invite stage handoff skipped assessment_id=%s result=%r",
                row.id,
                moved,
            )
            return _record_handoff_failure(
                db,
                assessment_id=int(row.id),
                generation=int(generation),
                error=_stable_provider_error("bullhorn", "stage_move", "skipped"),
                terminal=True,
            )
        fresh = _fresh_generation_row(
            db, assessment_id=int(row.id), generation=int(generation)
        )
        if fresh is None:
            db.rollback()
            return {"status": "superseded"}
        fresh.invite_workable_stage_moved_at = _now()
        db.commit()
        row = _load_handoff(db, int(row.id))
        if _stored_generation(row.invite_workable_handoff_generation) != int(
            generation
        ):
            return {"status": "superseded"}

    try:
        noted = execute_op(
            db,
            organization_id=int(row.organization_id),
            op_type=OP_POST_NOTE,
            payload={
                "application_id": int(row.application_id),
                "body": _assessment_handoff_note(row, generation),
                "actor_type": actor_type,
                "actor_id": actor_id,
                "source": source,
            },
        )
    except WorkableWritebackError as exc:
        logger.exception(
            "Bullhorn invite note handoff failed assessment_id=%s code=%s",
            row.id,
            exc.code,
        )
        return _record_handoff_failure(
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=_stable_provider_error("bullhorn", "note_post", exc.code),
            terminal=not bool(exc.retriable),
        )
    except Exception as exc:
        logger.exception(
            "Bullhorn invite note handoff raised assessment_id=%s error_type=%s",
            row.id,
            type(exc).__name__,
        )
        return _record_handoff_failure(
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=_stable_provider_error("bullhorn", "note_post"),
            terminal=False,
        )
    if noted.get("status") != "ok":
        logger.error(
            "Bullhorn invite note handoff skipped assessment_id=%s result=%r",
            row.id,
            noted,
        )
        return _record_handoff_failure(
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=_stable_provider_error("bullhorn", "note_post", "skipped"),
            terminal=True,
        )

    fresh = _fresh_generation_row(
        db, assessment_id=int(row.id), generation=int(generation)
    )
    if fresh is None:
        db.rollback()
        return {"status": "superseded"}
    fresh.invite_workable_note_posted_at = _now()
    fresh.invite_workable_handoff_status = HANDOFF_SUCCEEDED
    fresh.invite_workable_handoff_claimed_at = None
    fresh.invite_workable_handoff_next_attempt_at = None
    fresh.invite_workable_handoff_last_error = None
    fresh.invite_channel = "bullhorn_hybrid"
    db.commit()
    return {"status": HANDOFF_SUCCEEDED, "assessment_id": int(fresh.id)}


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
    handoff_intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    if str(handoff_intent.get("ats_handoff_provider") or "").lower() == "bullhorn":
        return _run_bullhorn_assessment_handoff(
            db,
            row=row,
            generation=int(generation),
        )
    org = row.organization
    stage = str(row.invite_workable_handoff_stage or "").strip()
    if settings.MVP_DISABLE_WORKABLE or org is None or not workable_writeback_enabled(org):
        return _skip_disabled(
            db, assessment_id=int(assessment_id), generation=int(generation)
        )
    if not row.workable_candidate_id:
        return _record_handoff_failure(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
            error="Workable candidate linkage is missing",
            terminal=True,
        )
    if not stage:
        stage, stage_error = resolve_workable_invite_stage(org, row.role)
        if not stage:
            return _record_handoff_failure(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=(
                    "needs_mapping: "
                    + (stage_error or "Workable invite stage is not configured")
                ),
                terminal=True,
            )
        row.invite_workable_handoff_stage = stage
        db.commit()

    if row.invite_workable_stage_moved_at is None:
        try:
            move_result = move_candidate_in_workable(
                org=org,
                candidate_id=str(row.workable_candidate_id),
                target_stage=stage,
                role=row.role,
            )
        except Exception as exc:
            logger.exception(
                "Workable invite stage handoff raised assessment_id=%s error_type=%s",
                assessment_id,
                type(exc).__name__,
            )
            return _record_handoff_failure(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=_stable_provider_error("workable", "stage_move"),
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
            logger.error(
                "Workable invite stage handoff failed assessment_id=%s "
                "code=%s result=%r",
                assessment_id,
                code,
                move_result,
            )
            return _record_handoff_failure(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=_stable_provider_error("workable", "stage_move", code),
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

    note = _assessment_handoff_note(row, generation)
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
        logger.exception(
            "Workable invite note handoff raised assessment_id=%s error_type=%s",
            assessment_id,
            type(exc).__name__,
        )
        result = {"success": False, "code": "provider_exception"}
    if not result.get("success"):
        code = result.get("code") or "api_error"
        logger.error(
            "Workable invite note handoff failed assessment_id=%s code=%s error=%s",
            assessment_id,
            code,
            result.get("error"),
        )
        return _record_handoff_failure(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
            error=_stable_provider_error("workable", "note_post", code),
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
    "assessment_invite_workable_handoff_context",
    "assessment_invite_workable_handoff_org",
    "defer_assessment_invite_workable_handoff",
    "run_assessment_invite_workable_handoff",
]
