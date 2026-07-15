"""Durable asynchronous scoring for persistent related-role evaluations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .celery_app import celery_app

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


@celery_app.task(
    name="app.tasks.sister_role_tasks.score_sister_evaluation",
    queue="scoring",
    soft_time_limit=600,
    time_limit=720,
)
def score_sister_evaluation(evaluation_id: int) -> dict:
    from ..cv_matching.holistic import run_holistic_match
    from ..models.role import Role
    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RUNNING,
        SISTER_EVAL_UNSCORABLE,
        SisterRoleEvaluation,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client
    from ..services.job_page_lifecycle import role_allows_new_paid_ats_work
    from ..services.sister_role_service import application_cv_text, text_fingerprint
    from ..services.workable_context_service import format_workable_context

    with SessionLocal() as db:
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(SisterRoleEvaluation.id == int(evaluation_id))
            .with_for_update(skip_locked=True)
            .first()
        )
        if evaluation is None:
            return {"status": "missing_or_locked", "evaluation_id": evaluation_id}
        if evaluation.status != SISTER_EVAL_PENDING:
            return {"status": "skipped", "evaluation_id": evaluation_id}

        application = evaluation.source_application
        source_role = (
            db.get(Role, int(application.role_id)) if application is not None else None
        )
        # This is deliberately immediately before the paid-call claim. Pause,
        # Turn off, role closure, or ATS closure therefore revokes queued work.
        if not role_allows_new_paid_ats_work(source_role, db=db):
            _set_retry(
                evaluation,
                error_code="authority_blocked",
                delay=_AUTHORITY_RECHECK_AFTER,
            )
            db.commit()
            return {
                "status": "authority_blocked",
                "evaluation_id": evaluation_id,
            }

        role = evaluation.role
        cv_text = application_cv_text(application) if application is not None else ""
        job_spec = (role.job_spec_text or "").strip() if role is not None else ""
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
            return {"status": SISTER_EVAL_UNSCORABLE, "evaluation_id": evaluation_id}

        evaluation.status = SISTER_EVAL_RUNNING
        evaluation.started_at = _now()
        evaluation.next_attempt_at = None
        evaluation.attempts = int(evaluation.attempts or 0) + 1
        evaluation.spec_fingerprint = text_fingerprint(job_spec)
        evaluation.cv_fingerprint = text_fingerprint(cv_text)
        db.commit()

        try:
            client = get_metered_client(organization_id=int(evaluation.organization_id))
            context = format_workable_context(application.candidate, application) or None
            output = run_holistic_match(
                cv_text,
                job_spec,
                client=client,
                metering_context={
                    "organization_id": int(evaluation.organization_id),
                    # Related roles are score-only projections over the owning
                    # ATS job. Charge and hard-admit every provider call against
                    # that operational role so its Agent budget covers the
                    # complete candidate workflow instead of creating an
                    # uncapped spend bucket on the projection role.
                    "role_id": int(source_role.id),
                    # Stable across retries so metering/caches can deduplicate a
                    # worker death after provider success but before row ack.
                    "entity_id": f"sister_evaluation:{evaluation.id}",
                },
                workable_context=context,
            )
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
            db.commit()
            return {
                "status": evaluation.status,
                "evaluation_id": evaluation_id,
                "score": evaluation.role_fit_score,
                "error_code": evaluation.last_error_code,
            }
        except Exception as exc:  # noqa: BLE001 - all transient failures persist
            error_type = type(exc).__name__
            logger.error(
                "Related-role scoring failed evaluation_id=%s error_code=provider_exception error_type=%s",
                evaluation_id,
                error_type,
            )
            db.rollback()
            evaluation = db.get(SisterRoleEvaluation, int(evaluation_id))
            if evaluation is not None and evaluation.status != SISTER_EVAL_DONE:
                _set_retry(
                    evaluation,
                    error_code=f"provider_exception_{error_type}",
                )
                db.commit()
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
    from ..models.sister_role_evaluation import SISTER_EVAL_PENDING, SisterRoleEvaluation
    from ..platform.database import SessionLocal

    with SessionLocal() as db:
        evaluation_ids = [
            int(row_id)
            for (row_id,) in db.query(SisterRoleEvaluation.id)
            .filter(
                SisterRoleEvaluation.role_id == int(role_id),
                SisterRoleEvaluation.status == SISTER_EVAL_PENDING,
            )
            .all()
        ]
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
    "recover_sister_role_evaluations",
    "score_sister_evaluation",
    "score_sister_role",
]
