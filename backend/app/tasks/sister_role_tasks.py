"""Durable asynchronous scoring for persistent related-role evaluations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from .celery_app import celery_app

if TYPE_CHECKING:
    from ..services.sister_role_scoring_generation import (
        LockedSisterScoreRows,
        SisterScoreGeneration,
        SisterScoreInputs,
    )

logger = logging.getLogger("taali.tasks.sister_roles")

_DISPATCH_STALE_AFTER = timedelta(minutes=5)
_RUNNING_STALE_AFTER = timedelta(minutes=15)
_AUTHORITY_RECHECK_AFTER = timedelta(minutes=5)
_FAST_RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
)
_SLOW_RETRY_DELAY = timedelta(hours=6)
_DETERMINISTIC_FAILURE_PREFIXES = (
    "client_init_failed",
    "empty_",
    "input_token_ceiling_exceeded",
    "missing_inputs",
    "prompt_render_failed",
    "validation_failed",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _at_or_before(value: datetime, cutoff: datetime) -> bool:
    """Compare SQLite-naive and Postgres-aware timestamps consistently."""

    if value.tzinfo is None and cutoff.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=None)
    return value <= cutoff


def _safe_code(value: object, *, fallback: str) -> str:
    """Reduce an untrusted error to a non-secret machine code."""

    prefix = str(value or "").strip().lower().split(":", 1)[0]
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", prefix).strip("_.-")
    return (normalized or fallback)[:100]


def _retry_delay(attempts: int) -> timedelta:
    index = max(int(attempts or 0) - 1, 0)
    if index < len(_FAST_RETRY_DELAYS):
        return _FAST_RETRY_DELAYS[index]
    return _SLOW_RETRY_DELAY


def _set_retry(
    evaluation,
    *,
    error_code: str,
    delay: timedelta | None = None,
) -> None:
    from ..models.sister_role_evaluation import SISTER_EVAL_RETRY_WAIT

    safe_code = _safe_code(error_code, fallback="scoring_failed")
    evaluation.status = SISTER_EVAL_RETRY_WAIT
    evaluation.next_attempt_at = _now() + (delay or _retry_delay(evaluation.attempts))
    evaluation.dispatch_attempted_at = None
    evaluation.started_at = None
    evaluation.scored_at = None
    evaluation.last_error_code = safe_code
    evaluation.error_message = safe_code


def _set_terminal_error(evaluation, *, error_code: str) -> None:
    from ..models.sister_role_evaluation import SISTER_EVAL_ERROR

    safe_code = _safe_code(error_code, fallback="deterministic_scoring_failure")
    evaluation.status = SISTER_EVAL_ERROR
    evaluation.next_attempt_at = None
    evaluation.dispatch_attempted_at = None
    evaluation.started_at = None
    evaluation.scored_at = _now()
    evaluation.last_error_code = safe_code
    evaluation.error_message = safe_code


def _is_deterministic_failure(error_code: str) -> bool:
    return any(error_code.startswith(prefix) for prefix in _DETERMINISTIC_FAILURE_PREFIXES)


def _provider_failure_code(value: object) -> str:
    normalized = str(value or "").strip().lower()
    for prefix in _DETERMINISTIC_FAILURE_PREFIXES:
        if prefix in normalized:
            return prefix
    return _safe_code(normalized, fallback="provider_scoring_failed")


def dispatch_sister_evaluation(
    db: Session,
    *,
    evaluation_id: int,
) -> dict:
    """Acquire a delivery lease, commit it, then publish one scoring task.

    A failed broker publish becomes a timed retry in the same durable row. The
    Beat sweep reclaims an accepted-but-never-run delivery after five minutes.
    """

    from ..models.sister_role_evaluation import (
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RETRY_WAIT,
        SISTER_EVAL_RUNNING,
        SisterRoleEvaluation,
    )

    now = _now()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.id == int(evaluation_id))
        .with_for_update(skip_locked=True)
        .first()
    )
    if evaluation is None:
        db.rollback()
        return {"status": "missing_or_locked", "evaluation_id": int(evaluation_id)}

    pending_due = evaluation.status == SISTER_EVAL_PENDING and (
        evaluation.dispatch_attempted_at is None
        or _at_or_before(
            evaluation.dispatch_attempted_at, now - _DISPATCH_STALE_AFTER
        )
    )
    retry_due = evaluation.status == SISTER_EVAL_RETRY_WAIT and (
        evaluation.next_attempt_at is None
        or _at_or_before(evaluation.next_attempt_at, now)
    )
    running_stale = evaluation.status == SISTER_EVAL_RUNNING and (
        evaluation.started_at is None
        or _at_or_before(evaluation.started_at, now - _RUNNING_STALE_AFTER)
    )
    if not (pending_due or retry_due or running_stale):
        db.rollback()
        return {"status": "not_due", "evaluation_id": int(evaluation_id)}

    evaluation.status = SISTER_EVAL_PENDING
    evaluation.queued_at = now
    evaluation.dispatch_attempted_at = now
    evaluation.next_attempt_at = None
    evaluation.started_at = None
    evaluation.last_error_code = None
    evaluation.error_message = None
    db.commit()

    try:
        score_sister_evaluation.apply_async(args=[int(evaluation_id)], queue="scoring")
    except Exception as exc:  # noqa: BLE001 - broker errors use durable retry state
        error_type = type(exc).__name__
        logger.error(
            "Related-role dispatch failed evaluation_id=%s error_code=queue_unavailable error_type=%s",
            evaluation_id,
            error_type,
        )
        db.rollback()
        current = db.get(SisterRoleEvaluation, int(evaluation_id))
        if current is not None and current.status == SISTER_EVAL_PENDING:
            _set_retry(
                current,
                error_code=f"queue_unavailable_{error_type}",
                delay=_FAST_RETRY_DELAYS[0],
            )
            db.commit()
        return {
            "status": SISTER_EVAL_RETRY_WAIT,
            "evaluation_id": int(evaluation_id),
            "error_code": "queue_unavailable",
            "error_type": error_type,
        }
    return {"status": "queued", "evaluation_id": int(evaluation_id)}


def _superseded_sister_score_result(
    evaluation_id: int, *, reason: str
) -> dict:
    return {
        "status": "superseded",
        "reason": reason,
        "evaluation_id": int(evaluation_id),
    }


def _claim_sister_score_generation(
    db: Session, *, evaluation_id: int
) -> tuple[
    SisterScoreGeneration | None,
    SisterScoreInputs | None,
    dict | None,
]:
    """Claim one paid attempt under the canonical generation-lock order."""

    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
        SISTER_EVAL_EXCLUDED,
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RUNNING,
        SISTER_EVAL_UNSCORABLE,
    )
    from ..services.sister_role_scoring_generation import (
        capture_sister_score_generation,
        capture_sister_score_inputs,
        locate_sister_score,
        lock_sister_score_rows,
    )
    from ..services.sister_role_service import (
        source_application_is_globally_advanced,
        source_application_is_globally_closed,
        transition_related_role_stage,
    )

    locator = locate_sister_score(db, evaluation_id=int(evaluation_id))
    if locator is None:
        db.rollback()
        return None, None, {
            "status": "missing_or_locked",
            "evaluation_id": int(evaluation_id),
        }
    # The locator is an unlocked identity read. End that transaction before
    # taking Organization -> Role -> Candidate -> Application -> evaluation.
    db.rollback()
    locked = lock_sister_score_rows(db, locator=locator, skip_locked=True)
    if locked is None:
        db.rollback()
        return None, None, {
            "status": "missing_or_locked",
            "evaluation_id": int(evaluation_id),
        }
    evaluation = locked.evaluation
    if evaluation.status != SISTER_EVAL_PENDING:
        db.rollback()
        return None, None, {
            "status": "skipped",
            "evaluation_id": int(evaluation_id),
        }
    role = locked.role
    application = locked.application
    # This is immediately before the paid-call claim. Pause, Turn off, role
    # closure, or ATS closure therefore revokes queued work.
    authority_hold = _hold_sister_score_if_authority_blocked(
        db,
        locked=locked,
    )
    if authority_hold is not None:
        return None, None, authority_hold
    if source_application_is_globally_closed(application):
        evaluation.status = SISTER_EVAL_EXCLUDED
        evaluation.error_message = "Shared ATS application is disqualified or closed"
        evaluation.last_error_code = "shared_application_closed"
        evaluation.next_attempt_at = None
        evaluation.dispatch_attempted_at = None
        evaluation.started_at = None
        evaluation.scored_at = _now()
        db.commit()
        return None, None, {
            "status": SISTER_EVAL_EXCLUDED,
            "evaluation_id": int(evaluation_id),
        }
    if source_application_is_globally_advanced(application):
        evaluation.status = SISTER_EVAL_DONE
        evaluation.next_attempt_at = None
        evaluation.dispatch_attempted_at = None
        evaluation.started_at = None
        transition_related_role_stage(
            evaluation, to_stage="advanced", source="system"
        )
        db.commit()
        return None, None, {
            "status": SISTER_EVAL_DONE,
            "reason": "shared_application_advanced",
            "evaluation_id": int(evaluation_id),
        }
    cv_text = (
        str(application.cv_text or "").strip()
        or str(locked.candidate.cv_text or "").strip()
    )
    job_spec = str(role.job_spec_text or "").strip()
    if not cv_text or not job_spec:
        code = "missing_cv_text" if not cv_text else "missing_job_specification"
        evaluation.status = SISTER_EVAL_UNSCORABLE
        evaluation.error_message = (
            "No CV text available" if not cv_text else "No job specification available"
        )
        evaluation.last_error_code = code
        evaluation.next_attempt_at = None
        evaluation.dispatch_attempted_at = None
        evaluation.scored_at = _now()
        db.commit()
        return None, None, {
            "status": SISTER_EVAL_UNSCORABLE,
            "evaluation_id": int(evaluation_id),
        }

    evaluation.status = SISTER_EVAL_RUNNING
    evaluation.started_at = _now()
    evaluation.next_attempt_at = None
    evaluation.attempts = int(evaluation.attempts or 0) + 1
    try:
        inputs = capture_sister_score_inputs(locked)
    except Exception as exc:  # noqa: BLE001 - malformed context is retryable
        _set_retry(
            evaluation,
            error_code=f"input_capture_exception_{type(exc).__name__}",
        )
        db.commit()
        return None, None, {
            "status": "retry_wait",
            "evaluation_id": int(evaluation_id),
            "error_code": "input_capture_exception",
            "error_type": type(exc).__name__,
        }
    evaluation.spec_fingerprint = inputs.spec_fingerprint
    evaluation.cv_fingerprint = inputs.cv_fingerprint
    generation = capture_sister_score_generation(locked, inputs)
    db.commit()
    return generation, inputs, None


def _supersede_sister_score_generation(
    db: Session,
    *,
    locked: LockedSisterScoreRows,
    expected: SisterScoreGeneration,
    current_inputs: SisterScoreInputs | None,
) -> dict:
    """Leave recruiter generation B intact or queue changed inputs afresh."""

    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
        SISTER_EVAL_EXCLUDED,
        SISTER_EVAL_PENDING,
        SISTER_EVAL_UNSCORABLE,
    )
    from ..services.sister_role_scoring_generation import (
        sister_score_attempt_is_current,
    )
    from ..services.sister_role_service import (
        source_application_is_globally_advanced,
        source_application_is_globally_closed,
        transition_related_role_stage,
    )

    evaluation = locked.evaluation
    if not sister_score_attempt_is_current(locked, expected=expected):
        # A reset/new worker owns this row now. Never mutate its state.
        db.rollback()
        return _superseded_sister_score_result(
            expected.locator.evaluation_id,
            reason="evaluation_generation_changed",
        )
    if current_inputs is None:
        db.rollback()
        return _superseded_sister_score_result(
            expected.locator.evaluation_id,
            reason="current_inputs_unavailable",
        )
    application = locked.application
    if source_application_is_globally_closed(application):
        evaluation.status = SISTER_EVAL_EXCLUDED
        evaluation.error_message = "Shared ATS application is disqualified or closed"
        evaluation.last_error_code = "shared_application_closed"
        evaluation.scored_at = _now()
    elif source_application_is_globally_advanced(application):
        evaluation.status = SISTER_EVAL_DONE
        transition_related_role_stage(
            evaluation, to_stage="advanced", source="system"
        )
    elif current_inputs is not None and (
        not current_inputs.cv_text or not current_inputs.job_spec
    ):
        evaluation.status = SISTER_EVAL_UNSCORABLE
        evaluation.error_message = (
            "No CV text available"
            if not current_inputs.cv_text
            else "No job specification available"
        )
        evaluation.last_error_code = (
            "missing_cv_text"
            if not current_inputs.cv_text
            else "missing_job_specification"
        )
        evaluation.scored_at = _now()
    else:
        evaluation.status = SISTER_EVAL_PENDING
        evaluation.spec_fingerprint = current_inputs.spec_fingerprint
        evaluation.cv_fingerprint = current_inputs.cv_fingerprint
        evaluation.role_fit_score = None
        evaluation.summary = None
        evaluation.details = None
        evaluation.error_message = "Inputs changed while scoring; queued a fresh attempt"
        evaluation.last_error_code = "inputs_superseded"
        evaluation.queued_at = _now()
        evaluation.scored_at = None
    evaluation.next_attempt_at = None
    evaluation.dispatch_attempted_at = None
    evaluation.started_at = None
    db.commit()
    return _superseded_sister_score_result(
        expected.locator.evaluation_id,
        reason="scoring_inputs_changed",
    )


def _hold_sister_score_if_authority_blocked(
    db: Session,
    *,
    locked: LockedSisterScoreRows,
) -> dict | None:
    """Persist an authority wait while every live authority row is locked."""

    from ..services.job_page_lifecycle import role_allows_new_paid_ats_work

    if role_allows_new_paid_ats_work(locked.role, db=db):
        return None
    _set_retry(
        locked.evaluation,
        error_code="authority_blocked",
        delay=_AUTHORITY_RECHECK_AFTER,
    )
    evaluation_id = int(locked.evaluation.id)
    db.commit()
    return {
        "status": "authority_blocked",
        "evaluation_id": evaluation_id,
    }


@celery_app.task(
    name="app.tasks.sister_role_tasks.score_sister_evaluation",
    queue="scoring",
    soft_time_limit=600,
    time_limit=720,
)
def score_sister_evaluation(evaluation_id: int) -> dict:
    from ..cv_matching.holistic import run_holistic_match
    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client
    from ..services.sister_role_scoring_generation import (
        lock_sister_score_rows,
        sister_score_generation_is_current,
    )

    with SessionLocal() as db:
        generation, inputs, terminal = _claim_sister_score_generation(
            db, evaluation_id=int(evaluation_id)
        )
        if terminal is not None:
            return terminal
        assert generation is not None and inputs is not None

        try:
            client = get_metered_client(
                organization_id=int(generation.locator.organization_id)
            )
            output = run_holistic_match(
                inputs.cv_text,
                inputs.job_spec,
                client=client,
                metering_context={
                    "organization_id": int(generation.locator.organization_id),
                    # Every related role owns an independent Agent and budget.
                    # The canonical application is shared, but this score/spend
                    # belongs to the role whose specification produced it.
                    "role_id": int(generation.locator.role_id),
                    # Stable across retries so metering/caches can deduplicate a
                    # worker death after provider success but before row ack.
                    "entity_id": f"sister_evaluation:{generation.locator.evaluation_id}",
                },
                workable_context=inputs.workable_context,
            )
            db.rollback()
            locked = lock_sister_score_rows(
                db,
                locator=generation.locator,
                skip_locked=False,
            )
            if locked is None:
                db.rollback()
                return _superseded_sister_score_result(
                    evaluation_id, reason="scoring_rows_unavailable"
                )
            is_current, current_inputs = sister_score_generation_is_current(
                locked, expected=generation
            )
            if not is_current:
                return _supersede_sister_score_generation(
                    db,
                    locked=locked,
                    expected=generation,
                    current_inputs=current_inputs,
                )
            authority_hold = _hold_sister_score_if_authority_blocked(
                db,
                locked=locked,
            )
            if authority_hold is not None:
                return authority_hold
            evaluation = locked.evaluation
            scoring_status = getattr(
                output.scoring_status, "value", str(output.scoring_status)
            )
            if str(scoring_status).lower() != "ok":
                error_code = _provider_failure_code(
                    getattr(output, "error_reason", None)
                )
                if _is_deterministic_failure(error_code):
                    _set_terminal_error(evaluation, error_code=error_code)
                else:
                    _set_retry(evaluation, error_code=error_code)
            else:
                evaluation.status = SISTER_EVAL_DONE
                evaluation.role_fit_score = output.role_fit_score
                evaluation.summary = (output.summary or "")[:4000] or None
                evaluation.details = output.model_dump(mode="json")
                evaluation.model_version = getattr(output, "model_version", None)
                evaluation.prompt_version = getattr(output, "prompt_version", None)
                evaluation.trace_id = getattr(output, "trace_id", None)
                evaluation.cache_hit = bool(getattr(output, "cache_hit", False))
                evaluation.error_message = None
                evaluation.last_error_code = None
                evaluation.next_attempt_at = None
                evaluation.dispatch_attempted_at = None
                evaluation.started_at = None
                evaluation.scored_at = _now()
            saved_status = str(evaluation.status)
            saved_score = evaluation.role_fit_score
            saved_error = evaluation.last_error_code
            saved_role_id = int(generation.locator.role_id)
            saved_evaluation_id = int(generation.locator.evaluation_id)
            db.commit()
            if saved_status == SISTER_EVAL_DONE:
                try:
                    related_role_agent_cycle.delay(
                        saved_role_id, evaluation_id=saved_evaluation_id
                    )
                except Exception:
                    # The score is durable; the role sweep can recover a
                    # missed decision kick without paying to score again.
                    logger.exception(
                        "Related-role decision kick failed evaluation_id=%s role_id=%s",
                        saved_evaluation_id,
                        saved_role_id,
                    )
            return {
                "status": saved_status,
                "evaluation_id": evaluation_id,
                "score": saved_score,
                "error_code": saved_error,
            }
        except Exception as exc:  # noqa: BLE001 - all transient failures persist
            error_type = type(exc).__name__
            db.rollback()
            locked = lock_sister_score_rows(
                db,
                locator=generation.locator,
                skip_locked=False,
            )
            if locked is not None:
                is_current, current_inputs = sister_score_generation_is_current(
                    locked, expected=generation
                )
            else:
                is_current, current_inputs = False, None
            if locked is not None and not is_current:
                return _supersede_sister_score_generation(
                    db,
                    locked=locked,
                    expected=generation,
                    current_inputs=current_inputs,
                )
            if locked is not None:
                authority_hold = _hold_sister_score_if_authority_blocked(
                    db,
                    locked=locked,
                )
                if authority_hold is not None:
                    return authority_hold
                logger.error(
                    "Related-role scoring failed evaluation_id=%s error_code=provider_exception error_type=%s",
                    evaluation_id,
                    error_type,
                )
                _set_retry(
                    locked.evaluation,
                    error_code=f"provider_exception_{error_type}",
                )
                db.commit()
            else:
                db.rollback()
                return _superseded_sister_score_result(
                    evaluation_id, reason="scoring_rows_unavailable"
                )
            return {
                "status": "retry_wait",
                "evaluation_id": evaluation_id,
                "error_code": "provider_exception",
                "error_type": error_type,
            }


@celery_app.task(
    name="app.tasks.sister_role_tasks.score_sister_role",
    queue="scoring",
)
def score_sister_role(role_id: int) -> dict:
    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RETRY_WAIT,
        SisterRoleEvaluation,
    )
    from ..platform.database import SessionLocal
    from ..services.sister_role_service import (
        source_application_is_globally_advanced,
        transition_related_role_stage,
    )

    with SessionLocal() as db:
        evaluations = (
            db.query(SisterRoleEvaluation)
            .options(joinedload(SisterRoleEvaluation.source_application))
            .filter(
                SisterRoleEvaluation.role_id == int(role_id),
                SisterRoleEvaluation.status.in_(
                    (SISTER_EVAL_PENDING, SISTER_EVAL_RETRY_WAIT)
                ),
            )
            .order_by(SisterRoleEvaluation.id.asc())
            .all()
        )
        # Ordinary role cycles can be triggered by any completed evaluation.
        # They must respect another row's provider backoff. Explicit recruiter
        # boundaries persist retry_wait -> pending before publishing this
        # backward-compatible one-argument task.
        dispatchable = []
        now = _now()
        for evaluation in evaluations:
            if source_application_is_globally_advanced(
                evaluation.source_application
            ):
                # A hand-off that races a queued/retry score is terminal for
                # the full related-role family. Preserve any prior score, stamp
                # the local projection, and never dispatch another paid call.
                evaluation.status = SISTER_EVAL_DONE
                evaluation.next_attempt_at = None
                evaluation.dispatch_attempted_at = None
                evaluation.started_at = None
                transition_related_role_stage(
                    evaluation, to_stage="advanced", source="system"
                )
                continue
            pending_due = evaluation.status == SISTER_EVAL_PENDING and (
                evaluation.dispatch_attempted_at is None
                or _at_or_before(
                    evaluation.dispatch_attempted_at,
                    now - _DISPATCH_STALE_AFTER,
                )
            )
            retry_due = evaluation.status == SISTER_EVAL_RETRY_WAIT and (
                evaluation.next_attempt_at is None
                or _at_or_before(evaluation.next_attempt_at, now)
            )
            if pending_due or retry_due:
                dispatchable.append(evaluation)
        db.commit()
        evaluation_ids = [int(evaluation.id) for evaluation in dispatchable]
        results = [
            dispatch_sister_evaluation(db, evaluation_id=evaluation_id)
            for evaluation_id in evaluation_ids
        ]
    return {
        "status": "queued",
        "role_id": role_id,
        "queued": sum(item["status"] == "queued" for item in results),
        "retrying": sum(item["status"] == "retry_wait" for item in results),
    }


@celery_app.task(
    name="app.tasks.sister_role_tasks.related_role_agent_cycle",
)
def related_role_agent_cycle(
    role_id: int,
    *,
    evaluation_id: int | None = None,
) -> dict:
    """Run the dedicated scoring/decision loop for one related role.

    Pending scores are dispatched to the scoring queue; already-complete local
    evaluations are materialised into this role's own assessment/decision
    funnel. Standard cohort code is deliberately never invoked here.
    """

    from ..models.role import ROLE_KIND_SISTER, Role
    from ..platform.database import SessionLocal
    from ..services.related_role_runtime import run_related_role_cycle
    from ..services.role_execution_guard import automatic_role_action_block_reason

    with SessionLocal() as db:
        role = (
            db.query(Role)
            .options(joinedload(Role.tasks))
            .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
            .one_or_none()
        )
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        if str(role.role_kind or "") != ROLE_KIND_SISTER:
            return {"status": "skipped", "reason": "not_related_role", "role_id": role_id}
        block_reason = automatic_role_action_block_reason(role, db=db)
        if block_reason is not None:
            return {"status": "skipped", "reason": block_reason, "role_id": role_id}

        scoring = score_sister_role.run(int(role.id))
        # score_sister_role owns a separate session. Refresh role state before
        # deciding already-complete evaluations in this session.
        db.expire_all()
        role = (
            db.query(Role)
            .options(joinedload(Role.tasks))
            .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
            .one()
        )
        decision = run_related_role_cycle(
            db,
            role=role,
            evaluation_id=evaluation_id,
        )
        if decision.get("status") == "ok" and not decision.get("created"):
            now = _now()
            role.agent_last_run_at = now
            role.agent_bootstrap_status = "ready"
            role.agent_bootstrap_error = None
            role.agent_bootstrap_completed_at = now
            db.commit()
        return {**decision, "scoring": scoring}


@celery_app.task(
    name="app.tasks.sister_role_tasks.recover_sister_role_evaluations",
)
def recover_sister_role_evaluations(*, limit: int = 200) -> dict:
    """Reclaim lost delivery, stale running, and due provider retry rows."""

    from ..models.sister_role_evaluation import (
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RETRY_WAIT,
        SISTER_EVAL_RUNNING,
        SisterRoleEvaluation,
    )
    from ..platform.database import SessionLocal

    now = _now()
    with SessionLocal() as db:
        ids = [
            int(row_id)
            for (row_id,) in db.query(SisterRoleEvaluation.id)
            .filter(
                or_(
                    (SisterRoleEvaluation.status == SISTER_EVAL_PENDING)
                    & (
                        SisterRoleEvaluation.dispatch_attempted_at.is_(None)
                        | (
                            SisterRoleEvaluation.dispatch_attempted_at
                            <= now - _DISPATCH_STALE_AFTER
                        )
                    ),
                    (SisterRoleEvaluation.status == SISTER_EVAL_RETRY_WAIT)
                    & (
                        SisterRoleEvaluation.next_attempt_at.is_(None)
                        | (SisterRoleEvaluation.next_attempt_at <= now)
                    ),
                    (SisterRoleEvaluation.status == SISTER_EVAL_RUNNING)
                    & (
                        SisterRoleEvaluation.started_at.is_(None)
                        | (
                            SisterRoleEvaluation.started_at
                            <= now - _RUNNING_STALE_AFTER
                        )
                    ),
                )
            )
            .order_by(SisterRoleEvaluation.id.asc())
            .limit(max(1, int(limit)))
            .all()
        ]
        results = [
            dispatch_sister_evaluation(db, evaluation_id=evaluation_id)
            for evaluation_id in ids
        ]
    return {
        "status": "complete",
        "recoverable": len(ids),
        "queued": sum(item["status"] == "queued" for item in results),
        "retrying": sum(item["status"] == "retry_wait" for item in results),
    }


__all__ = [
    "dispatch_sister_evaluation",
    "related_role_agent_cycle",
    "recover_sister_role_evaluations",
    "score_sister_evaluation",
    "score_sister_role",
]
