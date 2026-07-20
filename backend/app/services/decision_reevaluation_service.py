"""Role-aware score freshness counts and recruiter-triggered re-evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..cv_matching.holistic import is_engine_outdated
from ..domains.assessments_runtime.role_support import is_resolved
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .cv_score_orchestrator import score_is_outdated, supersede_pending_decisions_for_app
from .decision_role_context import is_cross_role_decision, load_related_evaluation
from .sister_role_evaluation_lifecycle import reset_evaluation_for_rescore
from .sister_role_service import application_cv_text


class RelatedEvaluationUnavailableError(RuntimeError):
    pass


def count_outdated_pending_decisions(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None = None,
    decision_types: list[str] | None = None,
) -> int:
    """Count old engines against each decision's own role scoring record."""

    query = (
        db.query(
            AgentDecision.role_id.label("decision_role_id"),
            CandidateApplication.role_id.label("application_role_id"),
            CandidateApplication.cv_match_details["engine_version"]
            .as_string()
            .label("engine_version"),
            CandidateApplication.cv_match_details["prompt_version"]
            .as_string()
            .label("prompt_version"),
            CandidateApplication.organization_id,
            CandidateApplication.application_outcome,
            CandidateApplication.pipeline_stage,
            CandidateApplication.cv_match_score,
            SisterRoleEvaluation.role_fit_score.label("related_role_fit_score"),
            SisterRoleEvaluation.details.label("related_details"),
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .outerjoin(
            SisterRoleEvaluation,
            and_(
                SisterRoleEvaluation.organization_id
                == AgentDecision.organization_id,
                SisterRoleEvaluation.role_id == AgentDecision.role_id,
                SisterRoleEvaluation.source_application_id
                == AgentDecision.application_id,
            ),
        )
        .filter(
            AgentDecision.organization_id == int(organization_id),
            AgentDecision.status.in_(
                ("pending", "reverted_for_feedback", "processing")
            ),
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= datetime.now(timezone.utc),
            ),
        )
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    if decision_types:
        query = query.filter(AgentDecision.decision_type.in_(decision_types))

    count = 0
    for row in query.all():
        shared_state = SimpleNamespace(
            organization_id=row.organization_id,
            application_outcome=row.application_outcome,
            pipeline_stage=row.pipeline_stage,
        )
        if is_resolved(shared_state):
            continue
        if int(row.decision_role_id) != int(row.application_role_id):
            details = row.related_details if isinstance(row.related_details, dict) else {}
            if row.related_role_fit_score is not None and is_engine_outdated(details):
                count += 1
            continue
        if row.cv_match_score is None:
            continue
        details: dict[str, str] = {}
        if row.engine_version:
            details["engine_version"] = row.engine_version
        if row.prompt_version:
            details["prompt_version"] = row.prompt_version
        standard_score = SimpleNamespace(
            organization_id=row.organization_id,
            cv_match_details=details,
            application_outcome=row.application_outcome,
            pipeline_stage=row.pipeline_stage,
            cv_match_score=row.cv_match_score,
        )
        try:
            if score_is_outdated(standard_score):
                count += 1
        except Exception:
            pass
    return count


def re_evaluate_related_decision(
    db: Session,
    *,
    decision: AgentDecision,
    application: CandidateApplication | None,
    role: Role | None,
    workspace_paused: bool,
) -> dict | None:
    """Refresh one related-role score, returning None for ordinary decisions."""

    if not is_cross_role_decision(decision, application):
        return None
    evaluation = load_related_evaluation(
        db, decision=decision, application=application
    )
    if role is None or evaluation is None or application is None:
        raise RelatedEvaluationUnavailableError

    superseded = supersede_pending_decisions_for_app(
        db,
        int(decision.application_id),
        reason="recruiter_requested_related_role_re_evaluate",
        role_id=int(decision.role_id),
    )
    dispatchable = reset_evaluation_for_rescore(
        evaluation,
        role_id=int(role.id),
        application_id=int(application.id),
        cv_text=application_cv_text(application),
        job_spec=str(role.job_spec_text or ""),
    )
    db.commit()

    blocked = bool(workspace_paused) or role.agent_paused_at is not None
    queued = False
    if dispatchable and not blocked:
        from ..tasks.sister_role_tasks import dispatch_sister_evaluation

        dispatch = dispatch_sister_evaluation(
            db, evaluation_id=int(evaluation.id)
        )
        queued = dispatch.get("status") == "queued"
        detail = (
            "re-scoring this role's evaluation; a fresh decision follows when scoring completes"
            if queued
            else "this role's score refresh is waiting for the scoring queue"
        )
    elif not dispatchable:
        detail = "this role cannot be re-scored until its CV and job specification are available"
    else:
        detail = "stale role decision discarded; score refresh waits while the agent is paused"

    return {
        "decision_id": int(decision.id),
        "role_id": int(decision.role_id),
        "application_id": int(decision.application_id),
        "superseded": superseded,
        "queued": queued,
        "detail": detail,
        "blocked": blocked or not dispatchable,
        "pause_scope": (
            "workspace"
            if workspace_paused
            else ("role" if role.agent_paused_at is not None else None)
        ),
    }


__all__ = [
    "RelatedEvaluationUnavailableError",
    "count_outdated_pending_decisions",
    "re_evaluate_related_decision",
]
