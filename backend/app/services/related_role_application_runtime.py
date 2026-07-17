"""Application/assessment seams for roles sharing one ATS application."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session, joinedload

from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


def related_role_for_application(
    db: Session,
    *,
    role_id: int,
    application: CandidateApplication,
) -> Role | None:
    return (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.ats_owner_role_id == int(application.role_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )


def assessment_uses_related_role_pipeline(db: Session, assessment) -> bool:
    role_id = getattr(assessment, "role_id", None)
    if not role_id:
        return False
    role = db.get(Role, int(role_id))
    return bool(role is not None and str(role.role_kind or "") == ROLE_KIND_SISTER)


def transition_related_role_assessment_stage(
    db: Session,
    *,
    assessment,
    to_stage: str,
    source: str,
) -> bool:
    if not getattr(assessment, "role_id", None) or not getattr(
        assessment, "application_id", None
    ):
        return False
    if not assessment_uses_related_role_pipeline(db, assessment):
        return False
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(assessment.role_id),
            SisterRoleEvaluation.source_application_id
            == int(assessment.application_id),
        )
        .one_or_none()
    )
    if evaluation is None:
        return False
    from .sister_role_service import (
        source_application_is_globally_advanced,
        source_application_is_globally_closed,
        transition_related_role_stage,
    )

    application = db.get(CandidateApplication, int(assessment.application_id))
    if source_application_is_globally_closed(
        application
    ) or source_application_is_globally_advanced(application):
        # This still counts as a handled related-role assessment. Returning
        # False would make the caller fall through and mutate the canonical
        # owner pipeline as though this were an ordinary assessment.
        return True

    transition_related_role_stage(evaluation, to_stage=to_stage, source=source)
    db.add(evaluation)
    return True


def advance_shared_application_family(
    db: Session,
    *,
    application: CandidateApplication,
    source: str,
) -> int:
    # One canonical application means every role-local card becomes moot once
    # that application is handed off. The decision currently being approved
    # is restamped approved by its caller after this reconciliation.
    from .pre_screen_decision_emitter import discard_pending_decisions_for_app

    discard_pending_decisions_for_app(
        db,
        application_id=int(application.id),
        reason="superseded: shared application advanced",
        include_processing=True,
    )
    evaluations = (
        db.query(SisterRoleEvaluation)
        .join(Role, Role.id == SisterRoleEvaluation.role_id)
        .filter(
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.ats_owner_role_id == int(application.role_id),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    from .sister_role_service import transition_related_role_stage

    for evaluation in evaluations:
        transition_related_role_stage(evaluation, to_stage="advanced", source=source)
    return len(evaluations)


def sync_shared_advance(
    db: Session,
    application: CandidateApplication,
    to_stage: str,
    source: str,
) -> int:
    if (
        str(to_stage or "").strip().lower() != "advanced"
        or str(application.pipeline_stage or "").strip().lower() != "advanced"
    ):
        return 0
    return advance_shared_application_family(
        db,
        application=application,
        source=source,
    )


def complete_timeout_pipeline(db: Session, *, assessment, application) -> None:
    """Apply timeout completion to the assessment's owning Taali funnel."""

    if assessment_uses_related_role_pipeline(db, assessment):
        transition_related_role_assessment_stage(
            db,
            assessment=assessment,
            to_stage="review",
            source="system",
        )
        return
    from ..domains.assessments_runtime.pipeline_service import (
        ensure_pipeline_fields,
        initialize_pipeline_event_if_missing,
        transition_stage,
    )
    from ..domains.assessments_runtime.role_support import (
        refresh_application_score_cache,
    )

    ensure_pipeline_fields(application)
    initialize_pipeline_event_if_missing(
        db,
        app=application,
        actor_type="system",
        reason="Pipeline initialized at timeout completion",
    )
    transition_stage(
        db,
        app=application,
        to_stage="review",
        source="system",
        actor_type="system",
        reason="Assessment auto-completed on timeout",
        metadata={"assessment_id": assessment.id, "completed_due_to_timeout": True},
    )
    refresh_application_score_cache(application, db=db)


def apply_related_role_runtime_projection(
    db: Session,
    *,
    projected: dict,
    payload: dict,
    sister_role: Role,
    evaluation: SisterRoleEvaluation | None,
    role_fit_score: float | None,
    application: CandidateApplication | None = None,
    assessments: list[Assessment] | None = None,
    pending_decision: AgentDecision | dict | None = None,
    runtime_preloaded: bool = False,
) -> dict:
    """Overlay this role's assessments and decision onto a projected row.

    Candidate-list callers preload the three role-owned runtime inputs once for
    the page and set ``runtime_preloaded``.  Detail callers omit them and retain
    the single-row query fallback.
    """

    from ..domains.assessments_runtime.role_support import (
        _assessment_history_for_application,
        _assessment_preview_for_application,
        _score_summary_from_active_assessments,
    )

    application_id = int(
        payload.get("id")
        or (evaluation.source_application_id if evaluation is not None else 0)
    )
    if runtime_preloaded:
        role_assessments = list(assessments or [])
    else:
        application = (
            db.get(CandidateApplication, application_id) if application_id else None
        )
        role_assessments = (
            db.query(Assessment)
            .options(joinedload(Assessment.task))
            .filter(
                Assessment.organization_id == int(sister_role.organization_id),
                Assessment.role_id == int(sister_role.id),
                Assessment.application_id == application_id,
            )
            .order_by(Assessment.created_at.desc(), Assessment.id.desc())
            .all()
            if application_id
            else []
        )
    active = [item for item in role_assessments if not bool(item.is_voided)]
    if application is not None:
        summary = _score_summary_from_active_assessments(application, active)
        completed = any(
            str(getattr(item.status, "value", item.status))
            in {"completed", "completed_due_to_timeout"}
            for item in active
        )
        if not completed:
            summary.update(
                {
                    "taali_score": role_fit_score,
                    "role_fit_score": role_fit_score,
                    "cv_fit_score": role_fit_score,
                    "assessment_score": None,
                    "mode": "sister_role",
                }
            )
        summary["score_provenance"] = {
            "source": "sister_role_evaluation",
            "label": "Related role fit",
        }
        projected["score_summary"] = summary
        projected["taali_score"] = summary.get("taali_score")
        projected["score_mode"] = summary.get("mode")
        projected["valid_assessment_id"] = summary.get("assessment_id")
        projected["valid_assessment_status"] = summary.get("assessment_status")
    if "assessment_history" in projected or role_assessments:
        proxy = SimpleNamespace(assessments=role_assessments)
        projected["assessment_preview"] = _assessment_preview_for_application(proxy)
        projected["assessment_history"] = _assessment_history_for_application(proxy)

    pending = pending_decision
    if not runtime_preloaded:
        pending = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.organization_id == int(sister_role.organization_id),
                AgentDecision.role_id == int(sister_role.id),
                AgentDecision.application_id == application_id,
                AgentDecision.status.in_(("pending", "processing")),
            )
            .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
            .first()
        )
    if pending is not None:
        projected["pending_decision"] = (
            dict(pending)
            if isinstance(pending, dict)
            else {
                "id": int(pending.id),
                "decision_type": pending.decision_type,
                "recommendation": pending.recommendation,
                "status": pending.status,
            }
        )
    return projected


def project_related_role_page(
    db: Session,
    *,
    sister_role: Role,
    applications: list[CandidateApplication],
    payloads: list[dict],
    assessments_preloaded: bool = False,
) -> list[dict]:
    """Project one candidate page with constant-count role-runtime queries."""

    if not applications:
        return payloads
    if len(applications) != len(payloads):
        raise ValueError("Related-role page applications and payloads must align")
    owner_role = (
        db.get(Role, int(sister_role.ats_owner_role_id))
        if sister_role.ats_owner_role_id
        else None
    )
    if owner_role is None:
        return payloads
    application_ids = [int(application.id) for application in applications]
    evaluation_map = {
        int(evaluation.source_application_id): evaluation
        for evaluation in db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.role_id == int(sister_role.id),
            SisterRoleEvaluation.source_application_id.in_(application_ids),
        )
    }
    assessment_map: dict[int, list[Assessment]] = {}
    if assessments_preloaded:
        role_assessments = (
            assessment
            for application in applications
            for assessment in (application.assessments or [])
            if int(assessment.role_id or 0) == int(sister_role.id)
            and int(assessment.organization_id or 0)
            == int(sister_role.organization_id)
        )
    else:
        role_assessments = iter(
            db.query(Assessment)
            .options(joinedload(Assessment.task))
            .filter(
                Assessment.organization_id == int(sister_role.organization_id),
                Assessment.role_id == int(sister_role.id),
                Assessment.application_id.in_(application_ids),
            )
            .order_by(
                Assessment.application_id.asc(),
                Assessment.created_at.desc(),
                Assessment.id.desc(),
            )
            .all()
        )
    for assessment in role_assessments:
        if assessment.application_id is not None:
            assessment_map.setdefault(int(assessment.application_id), []).append(
                assessment
            )
    if assessments_preloaded:
        for assessments in assessment_map.values():
            assessments.sort(
                key=lambda assessment: (
                    assessment.created_at.timestamp()
                    if assessment.created_at is not None
                    else 0.0,
                    int(assessment.id or 0),
                ),
                reverse=True,
            )

    from .pending_decision_projection import pending_decision_map
    from .sister_role_projection import project_sister_application

    decision_map = pending_decision_map(
        db,
        application_ids,
        role_id=int(sister_role.id),
        statuses=("pending", "processing"),
    )
    return [
        project_sister_application(
            payload,
            sister_role=sister_role,
            owner_role=owner_role,
            evaluation=evaluation_map.get(int(application.id)),
            db=db,
            application=application,
            assessments=assessment_map.get(int(application.id), []),
            pending_decision=decision_map.get(int(application.id)),
            runtime_preloaded=True,
        )
        for application, payload in zip(applications, payloads, strict=True)
    ]


__all__ = [
    "advance_shared_application_family",
    "apply_related_role_runtime_projection",
    "assessment_uses_related_role_pipeline",
    "complete_timeout_pipeline",
    "project_related_role_page",
    "related_role_for_application",
    "sync_shared_advance",
    "transition_related_role_assessment_stage",
]
