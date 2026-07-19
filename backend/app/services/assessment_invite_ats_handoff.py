"""Generation-exact ATS stage and note handoff after confirmed invite email."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..platform.config import settings
from .ats_stage_move_dispatch_snapshot import build_stage_move_dispatch_payload
from .workable_actions_service import (
    WorkableWritebackError,
    resolve_workable_invite_stage,
    workable_can_write_candidates,
    workable_writeback_enabled,
)
from .workable_op_runner import OP_MOVE_STAGE, OP_POST_NOTE

logger = logging.getLogger("taali.assessment_invite_ats_handoff")


def _helpers():
    from .assessment_invite_workable_handoff import (
        HANDOFF_SKIPPED,
        HANDOFF_SUCCEEDED,
        _assessment_handoff_note,
        _fresh_generation_row,
        _load_handoff,
        _now,
        _record_handoff_failure,
        _skip_disabled,
        _stable_provider_error,
        _stored_generation,
    )

    return {
        "skipped": HANDOFF_SKIPPED,
        "succeeded": HANDOFF_SUCCEEDED,
        "note": _assessment_handoff_note,
        "fresh": _fresh_generation_row,
        "load": _load_handoff,
        "now": _now,
        "fail": _record_handoff_failure,
        "skip": _skip_disabled,
        "error": _stable_provider_error,
        "generation": _stored_generation,
    }


def _configured(row: Assessment, *, provider: str) -> bool:
    org = row.organization
    if org is None:
        return False
    if provider == "workable":
        return bool(
            not settings.MVP_DISABLE_WORKABLE
            and workable_writeback_enabled(org)
            and workable_can_write_candidates(org)
            and org.workable_access_token
            and org.workable_subdomain
        )
    return bool(
        settings.BULLHORN_ENABLED
        and org.bullhorn_connected
        and org.bullhorn_username
        and org.bullhorn_client_id
        and org.bullhorn_client_secret
        and org.bullhorn_refresh_token
    )


def _targets(row: Assessment, *, provider: str) -> tuple[str, str] | None:
    app = row.application
    candidate = row.candidate
    if app is None or candidate is None:
        return None
    if provider == "workable":
        target = str(app.workable_candidate_id or "").strip()
        return (target, target) if target else None
    app_target = str(app.bullhorn_job_submission_id or "").strip()
    candidate_target = str(candidate.bullhorn_candidate_id or "").strip()
    return (app_target, candidate_target) if app_target and candidate_target else None


def _failure_terminal(error: WorkableWritebackError) -> bool:
    return not bool(
        error.retriable and getattr(error, "provider_called", None) is False
    )


def _defer_for_lost_mutex(
    db: Session,
    *,
    row: Assessment,
    generation: int,
    provider: str,
) -> dict:
    h = _helpers()
    return h["fail"](
        db,
        assessment_id=int(row.id),
        generation=int(generation),
        error=f"{provider}_mutex_lease_lost",
        terminal=False,
    )


def _run_stage(
    db: Session,
    *,
    row: Assessment,
    generation: int,
    provider: str,
    stage: str,
    actor_type: str,
    actor_id: int | None,
    source: str,
    should_yield: Callable[[], bool] | None,
) -> dict:
    h = _helpers()
    from .workable_op_runner import execute_op

    payload = build_stage_move_dispatch_payload(
        app=row.application,
        provider=provider,
        target_stage=stage,
        operation_id=(
            f"assessment-stage-move:{provider}:{int(row.id)}:{int(generation)}"
        ),
    )
    if should_yield is not None and should_yield():
        return _defer_for_lost_mutex(
            db, row=row, generation=generation, provider=provider
        )
    try:
        result = execute_op(
            db,
            organization_id=int(row.organization_id),
            op_type=OP_MOVE_STAGE,
            payload={
                **payload,
                "reason": f"Confirmed assessment invite handed off to {provider.title()}",
                "actor_type": actor_type,
                "actor_id": actor_id,
                "source": source,
            },
            should_yield=should_yield,
        )
    except WorkableWritebackError as exc:
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "stage_move", exc.code),
            terminal=_failure_terminal(exc),
        )
    except Exception as exc:
        logger.exception(
            "Invite stage handoff raised assessment_id=%s error_type=%s",
            row.id,
            type(exc).__name__,
        )
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "stage_move"),
            terminal=True,
        )
    if result.get("status") != "ok":
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "stage_move", "skipped"),
            terminal=True,
        )
    fresh = h["fresh"](db, assessment_id=int(row.id), generation=int(generation))
    if fresh is None:
        db.rollback()
        return {"status": "superseded"}
    fresh.invite_workable_stage_moved_at = h["now"]()
    db.commit()
    return {"status": "ok"}


def _run_note(
    db: Session,
    *,
    row: Assessment,
    generation: int,
    provider: str,
    application_target: str,
    candidate_target: str,
    actor_type: str,
    actor_id: int | None,
    source: str,
    should_yield: Callable[[], bool] | None,
) -> dict:
    h = _helpers()
    from .ats_note_dispatch import (
        AtsNoteQueueError,
        prepare_application_ats_note_payload,
    )
    from .workable_op_runner import execute_op

    if should_yield is not None and should_yield():
        return _defer_for_lost_mutex(
            db, row=row, generation=generation, provider=provider
        )
    try:
        payload = prepare_application_ats_note_payload(
            db,
            organization_id=int(row.organization_id),
            application_id=int(row.application_id),
            body=h["note"](row, generation),
            provider=provider,
            actor_type=actor_type,
            actor_id=actor_id,
            expected_provider_target_id=application_target,
            expected_candidate_provider_id=candidate_target,
        )
        payload.update(
            note_operation_id=(
                f"assessment-note:{provider}:{int(row.id)}:{int(generation)}"
            ),
            source=source,
        )
        result = execute_op(
            db,
            organization_id=int(row.organization_id),
            op_type=OP_POST_NOTE,
            payload=payload,
            should_yield=should_yield,
        )
    except AtsNoteQueueError as exc:
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "note_post", exc.code),
            terminal=True,
        )
    except WorkableWritebackError as exc:
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "note_post", exc.code),
            terminal=_failure_terminal(exc),
        )
    except Exception as exc:
        logger.exception(
            "Invite note handoff raised assessment_id=%s error_type=%s",
            row.id,
            type(exc).__name__,
        )
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](provider, "note_post"),
            terminal=True,
        )
    if result.get("status") not in {"ok", "already_completed"}:
        retry_safe = (
            result.get("status") == "failed"
            and result.get("provider_called") is False
            and result.get("retriable") is True
        )
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=h["error"](
                provider,
                "note_post",
                str(result.get("code") or "api_error"),
            ),
            terminal=not retry_safe,
        )
    return {"status": "ok"}


def run_assessment_invite_ats_handoff(
    db: Session,
    *,
    row: Assessment,
    generation: int,
    provider: str,
    should_yield: Callable[[], bool] | None = None,
) -> dict:
    """Execute generation-bound stage then note receipts without resending email."""

    h = _helpers()
    provider = "bullhorn" if provider == "bullhorn" else "workable"
    if not _configured(row, provider=provider):
        if provider == "workable":
            return h["skip"](db, assessment_id=int(row.id), generation=int(generation))
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error="bullhorn_unavailable",
            terminal=True,
        )
    targets = _targets(row, provider=provider)
    if targets is None:
        return h["fail"](
            db,
            assessment_id=int(row.id),
            generation=int(generation),
            error=f"{provider}_linkage_missing",
            terminal=True,
        )
    stage = str(row.invite_workable_handoff_stage or "").strip()
    if not stage and provider == "workable":
        stage, detail = resolve_workable_invite_stage(row.organization, row.role)
        if not stage:
            return h["fail"](
                db,
                assessment_id=int(row.id),
                generation=int(generation),
                error=f"needs_mapping: {detail or 'Workable invite stage is not configured'}",
                terminal=True,
            )
        row.invite_workable_handoff_stage = stage
        db.commit()
    if not stage:
        stage = "invited"
    intent = (
        row.invite_pipeline_transition
        if isinstance(row.invite_pipeline_transition, dict)
        else {}
    )
    actor_type = str(intent.get("actor_type") or "system")
    actor_id = intent.get("actor_id")
    source = str(intent.get("source") or actor_type)
    if row.invite_workable_stage_moved_at is None:
        result = _run_stage(
            db,
            row=row,
            generation=generation,
            provider=provider,
            stage=stage,
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            should_yield=should_yield,
        )
        if result.get("status") != "ok":
            return result
        row = h["load"](db, int(row.id))
        if h["generation"](row.invite_workable_handoff_generation) != int(generation):
            return {"status": "superseded"}
        if should_yield is not None and should_yield():
            return _defer_for_lost_mutex(
                db, row=row, generation=generation, provider=provider
            )
    application_target, candidate_target = targets
    result = _run_note(
        db,
        row=row,
        generation=generation,
        provider=provider,
        application_target=application_target,
        candidate_target=candidate_target,
        actor_type=actor_type,
        actor_id=actor_id,
        source=source,
        should_yield=should_yield,
    )
    if result.get("status") != "ok":
        return result
    fresh = h["fresh"](db, assessment_id=int(row.id), generation=int(generation))
    if fresh is None:
        db.rollback()
        return {"status": "superseded"}
    fresh.invite_workable_note_posted_at = h["now"]()
    fresh.invite_workable_handoff_status = h["succeeded"]
    fresh.invite_workable_handoff_claimed_at = None
    fresh.invite_workable_handoff_next_attempt_at = None
    fresh.invite_workable_handoff_last_error = None
    fresh.invite_channel = f"{provider}_hybrid"
    db.commit()
    return {"status": h["succeeded"], "assessment_id": int(fresh.id)}


__all__ = ["run_assessment_invite_ats_handoff"]
