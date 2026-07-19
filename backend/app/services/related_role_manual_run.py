"""Decision handoff for recruiter-triggered related-role runs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.sister_role_evaluation import SISTER_EVAL_DONE, SisterRoleEvaluation
from .manual_agent_run_dispatch import finish_manual_run_intent
from .related_role_runtime import run_related_role_cycle


def materialize_completed_manual_scope(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    application_id: int | None,
) -> dict | None:
    """Materialize completed scores in the exact recruiter-confirmed scope."""

    query = db.query(SisterRoleEvaluation.id).filter(
        SisterRoleEvaluation.organization_id == int(organization_id),
        SisterRoleEvaluation.role_id == int(role.id),
        SisterRoleEvaluation.status == SISTER_EVAL_DONE,
    )
    if application_id is not None:
        query = query.filter(
            SisterRoleEvaluation.source_application_id == int(application_id)
        )
    evaluation_id = query.order_by(SisterRoleEvaluation.id.asc()).limit(1).scalar()
    if evaluation_id is None:
        return None
    return run_related_role_cycle(
        db,
        role=role,
        evaluation_id=int(evaluation_id) if application_id is not None else None,
    )


def finish_manual_related_run(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    application_id: int | None,
    dispatch_key: str,
    dispatch_results: list[dict],
    deferred: int,
    materialized: dict | None,
) -> dict:
    """Terminalize the durable handoff and report its actual delivery state."""

    result: dict = {"role_id": int(role.id)}
    if application_id is not None:
        result["application_id"] = int(application_id)
    if materialized is not None:
        materialized_status = str(materialized.get("status") or "error")
        if materialized_status != "ok":
            reason = str(
                materialized.get("reason")
                or "related_role_decision_materialization_failed"
            )
            finish_manual_run_intent(
                db,
                dispatch_key=dispatch_key,
                organization_id=int(organization_id),
                role_id=int(role.id),
                application_id=application_id,
                status="aborted",
                error=reason,
            )
            db.commit()
            return {**result, "status": "skipped", "reason": reason}
        result["materialized"] = materialized

    queued = sum(item.get("status") == "queued" for item in dispatch_results)
    retrying = int(deferred) + sum(
        item.get("status") == "retry_wait" for item in dispatch_results
    )
    in_progress = sum(
        item.get("status") in {"missing_or_locked", "not_due", "skipped"}
        for item in dispatch_results
    )
    result.update({"queued": queued, "retrying": retrying})
    if materialized is not None and bool(materialized.get("has_more")):
        result["status"] = "in_progress"
        result["recovery"] = "recover_dispatching_manual_agent_runs"
        return result
    if queued:
        result["status"] = "queued"
    elif retrying:
        result["status"] = "deferred"
        result["recovery"] = "recover_sister_role_evaluations"
    elif in_progress:
        result["status"] = "in_progress"
    elif materialized is not None:
        result["status"] = "completed"
    else:
        result["status"] = "no_work"

    if finish_manual_run_intent(
        db,
        dispatch_key=dispatch_key,
        organization_id=int(organization_id),
        role_id=int(role.id),
        application_id=application_id,
        status="succeeded",
    ):
        db.commit()
    return result


__all__ = ["finish_manual_related_run", "materialize_completed_manual_scope"]
