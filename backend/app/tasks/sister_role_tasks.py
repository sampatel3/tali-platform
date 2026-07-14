"""Asynchronous scoring for persistent sister-role evaluations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.sister_roles")


@celery_app.task(
    name="app.tasks.sister_role_tasks.score_sister_evaluation",
    queue="scoring",
    max_retries=0,
)
def score_sister_evaluation(evaluation_id: int) -> dict:
    from ..cv_matching.holistic import run_holistic_match
    from ..models.sister_role_evaluation import (
        SISTER_EVAL_DONE,
        SISTER_EVAL_ERROR,
        SISTER_EVAL_PENDING,
        SISTER_EVAL_RUNNING,
        SISTER_EVAL_UNSCORABLE,
        SisterRoleEvaluation,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client
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

        role = evaluation.role
        application = evaluation.source_application
        cv_text = application_cv_text(application)
        job_spec = (role.job_spec_text or "").strip()
        if not cv_text or not job_spec:
            evaluation.status = SISTER_EVAL_UNSCORABLE
            evaluation.error_message = "No CV text available" if not cv_text else "No job specification available"
            evaluation.scored_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": SISTER_EVAL_UNSCORABLE, "evaluation_id": evaluation_id}

        evaluation.status = SISTER_EVAL_RUNNING
        evaluation.started_at = datetime.now(timezone.utc)
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
                    "role_id": int(role.id),
                    "entity_id": f"sister_evaluation:{evaluation.id}",
                },
                workable_context=context,
            )
            scoring_status = getattr(output.scoring_status, "value", str(output.scoring_status))
            if str(scoring_status).lower() != "ok":
                evaluation.status = SISTER_EVAL_ERROR
                evaluation.error_message = (output.error_reason or "Sister-role scoring failed")[:1000]
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
            evaluation.scored_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "status": evaluation.status,
                "evaluation_id": evaluation_id,
                "score": evaluation.role_fit_score,
            }
        except Exception as exc:  # noqa: BLE001 - persist a per-row failure
            logger.exception("Sister evaluation %s failed", evaluation_id)
            db.rollback()
            evaluation = db.get(SisterRoleEvaluation, int(evaluation_id))
            if evaluation is not None:
                evaluation.status = SISTER_EVAL_ERROR
                evaluation.error_message = str(exc)[:1000]
                evaluation.scored_at = datetime.now(timezone.utc)
                db.commit()
            return {"status": SISTER_EVAL_ERROR, "evaluation_id": evaluation_id}


@celery_app.task(
    name="app.tasks.sister_role_tasks.score_sister_role",
    queue="scoring",
    max_retries=0,
)
def score_sister_role(role_id: int) -> dict:
    from ..models.sister_role_evaluation import SISTER_EVAL_PENDING, SisterRoleEvaluation
    from ..platform.database import SessionLocal

    with SessionLocal() as db:
        evaluation_ids = [
            int(row_id)
            for (row_id,) in db.query(SisterRoleEvaluation.id).filter(
                SisterRoleEvaluation.role_id == int(role_id),
                SisterRoleEvaluation.status == SISTER_EVAL_PENDING,
            ).all()
        ]
    failed_ids: list[int] = []
    for evaluation_id in evaluation_ids:
        try:
            score_sister_evaluation.apply_async(args=[evaluation_id], queue="scoring")
        except Exception:  # pragma: no cover - broker failure path
            logger.exception("Failed to dispatch sister evaluation %s", evaluation_id)
            failed_ids.append(evaluation_id)
    if failed_ids:
        from ..models.sister_role_evaluation import SISTER_EVAL_ERROR

        with SessionLocal() as db:
            db.query(SisterRoleEvaluation).filter(
                SisterRoleEvaluation.id.in_(failed_ids)
            ).update(
                {
                    SisterRoleEvaluation.status: SISTER_EVAL_ERROR,
                    SisterRoleEvaluation.error_message: "Scoring worker unavailable; retry the roster",
                },
                synchronize_session=False,
            )
            db.commit()
    return {
        "status": "queued",
        "role_id": role_id,
        "queued": len(evaluation_ids) - len(failed_ids),
        "dispatch_errors": len(failed_ids),
    }
