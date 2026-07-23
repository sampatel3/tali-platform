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
from .decision_role_context import is_cross_role_decision
from .sister_role_evaluation_lifecycle import reset_evaluation_for_rescore
from .sister_role_service import application_cv_text


class RelatedEvaluationUnavailableError(RuntimeError):
    pass


class RelatedApplicationResolvedError(RuntimeError):
    pass


class RelatedDecisionNotActionableError(RuntimeError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


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
            Role.role_kind.label("decision_role_kind"),
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
            SisterRoleEvaluation.id.label("related_evaluation_id"),
            SisterRoleEvaluation.application_outcome.label("related_outcome"),
            SisterRoleEvaluation.pipeline_stage.label("related_stage"),
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .join(Role, Role.id == AgentDecision.role_id)
        .outerjoin(
            SisterRoleEvaluation,
            and_(
                SisterRoleEvaluation.organization_id
                == AgentDecision.organization_id,
                SisterRoleEvaluation.role_id == AgentDecision.role_id,
                SisterRoleEvaluation.source_application_id
                == AgentDecision.application_id,
                SisterRoleEvaluation.deleted_at.is_(None),
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
        if row.related_evaluation_id is not None:
            role_state = SimpleNamespace(
                organization_id=row.organization_id,
                application_outcome=row.related_outcome,
                pipeline_stage=row.related_stage,
            )
            if is_resolved(role_state):
                continue
            details = row.related_details if isinstance(row.related_details, dict) else {}
            if row.related_role_fit_score is not None and is_engine_outdated(details):
                count += 1
            continue
        if str(row.decision_role_kind or "") == "sister":
            # A related-role decision without its explicit membership cannot be
            # proven active or fresh. Fail closed instead of consulting the
            # source application's owner-role state.
            continue
        shared_state = SimpleNamespace(
            organization_id=row.organization_id,
            application_outcome=row.application_outcome,
            pipeline_stage=row.pipeline_stage,
        )
        if is_resolved(shared_state):
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
    """Refresh one related-role score under the terminal-action lock order.

    The initial ORM arguments establish identity only.  Every mutable fact is
    reloaded under Organization -> ordered Roles -> logical Application -> ATS
    transport Application -> Evaluation -> Decision.  This linearizes a
    recruiter re-evaluation with approval and terminal stage/outcome changes.
    """

    related = bool(
        role is not None and str(role.role_kind or "") == "sister"
    ) or is_cross_role_decision(decision, application)
    if not related:
        return None

    decision_id = int(decision.id)
    organization_id = int(decision.organization_id)
    role_id = int(decision.role_id)
    application_id = int(decision.application_id)
    role_identity = (
        db.query(Role.ats_owner_role_id)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.role_kind == "sister",
        )
        .one_or_none()
    )
    evaluation_identity = (
        db.query(
            SisterRoleEvaluation.id,
            SisterRoleEvaluation.ats_application_id,
        )
        .filter(
            SisterRoleEvaluation.organization_id == organization_id,
            SisterRoleEvaluation.role_id == role_id,
            SisterRoleEvaluation.source_application_id == application_id,
        )
        .one_or_none()
    )
    if role_identity is None or evaluation_identity is None:
        raise RelatedEvaluationUnavailableError

    owner_role_id = (
        int(role_identity.ats_owner_role_id)
        if role_identity.ats_owner_role_id is not None
        else None
    )
    evaluation_id = int(evaluation_identity.id)
    ats_application_id = (
        int(evaluation_identity.ats_application_id)
        if evaluation_identity.ats_application_id is not None
        else None
    )
    # The route performed only reads before entering this service. End that
    # snapshot so lock acquisition always starts at Organization.
    db.rollback()

    from .decision_membership import lock_resolution_roles
    from .workspace_agent_control import workspace_agent_control_snapshot

    paused, _workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=organization_id,
        lock=True,
    )
    role_ids = {role_id}
    if owner_role_id is not None:
        role_ids.add(owner_role_id)
    locked_roles = lock_resolution_roles(
        db,
        organization_id=organization_id,
        role_ids=role_ids,
    )
    locked_role = locked_roles.get(role_id)
    if locked_role is None or str(locked_role.role_kind or "") != "sister":
        raise RelatedEvaluationUnavailableError

    locked_application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if locked_application is None:
        raise RelatedEvaluationUnavailableError
    if ats_application_id is not None and ats_application_id != application_id:
        # Missing/deleted ATS transport does not revoke local scoring
        # authority. Lock the row when present solely to stabilize linkage.
        (
            db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.id == ats_application_id,
                CandidateApplication.organization_id == organization_id,
                CandidateApplication.candidate_id
                == int(locked_application.candidate_id),
                CandidateApplication.role_id == locked_role.ats_owner_role_id,
            )
            .with_for_update(of=CandidateApplication)
            .scalar()
        )
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.id == evaluation_id,
            SisterRoleEvaluation.organization_id == organization_id,
            SisterRoleEvaluation.role_id == role_id,
            SisterRoleEvaluation.source_application_id == application_id,
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .with_for_update(of=SisterRoleEvaluation)
        .populate_existing()
        .one_or_none()
    )
    if evaluation is None:
        raise RelatedEvaluationUnavailableError
    if (
        str(evaluation.application_outcome or "open").strip().lower() != "open"
        or str(evaluation.pipeline_stage or "applied").strip().lower()
        == "advanced"
    ):
        raise RelatedApplicationResolvedError

    locked_decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
            AgentDecision.role_id == role_id,
            AgentDecision.application_id == application_id,
        )
        .with_for_update(of=AgentDecision)
        .populate_existing()
        .one_or_none()
    )
    if locked_decision is None:
        raise RelatedEvaluationUnavailableError
    if locked_decision.status not in ("pending", "reverted_for_feedback"):
        raise RelatedDecisionNotActionableError(str(locked_decision.status))

    superseded = supersede_pending_decisions_for_app(
        db,
        application_id,
        reason="recruiter_requested_related_role_re_evaluate",
        role_id=role_id,
    )
    dispatchable = reset_evaluation_for_rescore(
        evaluation,
        role_id=role_id,
        application_id=application_id,
        cv_text=application_cv_text(locked_application),
        job_spec=str(locked_role.job_spec_text or ""),
    )
    db.commit()

    # Kept in the public signature for rolling callers; locked database state
    # above is authoritative over the pre-lock route snapshot.
    del workspace_paused
    blocked = bool(paused) or locked_role.agent_paused_at is not None
    queued = False
    if dispatchable and not blocked:
        from ..tasks.sister_role_tasks import dispatch_sister_evaluation

        dispatch = dispatch_sister_evaluation(db, evaluation_id=evaluation_id)
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
        "decision_id": decision_id,
        "role_id": role_id,
        "application_id": application_id,
        "superseded": superseded,
        "queued": queued,
        "detail": detail,
        "blocked": blocked or not dispatchable,
        "pause_scope": (
            "workspace"
            if paused
            else ("role" if locked_role.agent_paused_at is not None else None)
        ),
    }


__all__ = [
    "RelatedApplicationResolvedError",
    "RelatedDecisionNotActionableError",
    "RelatedEvaluationUnavailableError",
    "count_outdated_pending_decisions",
    "re_evaluate_related_decision",
]
