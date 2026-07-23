"""Application/assessment seams for independent related-role applications."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


@dataclass(frozen=True)
class RelatedRoleAssessmentContext:
    """Locked role-local rows for one assessment-derived transition.

    ``handled`` means the assessment must never fall through to the source
    application's ordinary pipeline.  A handled context can be blocked (for
    example, because the membership was deleted or already resolved) while the
    provider/assessment receipt itself is still allowed to commit truthfully.
    """

    handled: bool
    reason: str | None = None
    role: Role | None = None
    application: CandidateApplication | None = None
    ats_application: CandidateApplication | None = None
    evaluation: SisterRoleEvaluation | None = None
    assessment: Assessment | None = None


@dataclass(frozen=True)
class RelatedRoleAssessmentTransitionResult:
    """Outcome of a role-local assessment stage transition."""

    handled: bool
    changed: bool = False
    reason: str | None = None
    role_id: int | None = None
    evaluation_id: int | None = None


def related_role_for_application(
    db: Session,
    *,
    role_id: int,
    application: CandidateApplication,
) -> Role | None:
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is None:
        return None
    membership = (
        db.query(SisterRoleEvaluation.id)
        .filter(
            SisterRoleEvaluation.organization_id
            == int(application.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .scalar()
    )
    return role if membership is not None else None


def related_role_evaluation_for_application(
    db: Session,
    *,
    role_id: int,
    application: CandidateApplication,
) -> SisterRoleEvaluation | None:
    """Load the exact explicit membership for a related role candidate."""

    role = related_role_for_application(
        db,
        role_id=int(role_id),
        application=application,
    )
    if role is None:
        return None
    return (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id
            == int(application.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .one_or_none()
    )


def role_application_is_resolved(
    db: Session,
    *,
    role_id: int,
    application: CandidateApplication,
) -> bool:
    """Return terminal state for the logical role, not its ATS transport.

    Missing explicit membership fails closed for a related role. Ordinary
    roles retain the canonical application-stage/outcome contract.
    """

    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(application.organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is not None and str(role.role_kind or "") == ROLE_KIND_SISTER:
        evaluation = related_role_evaluation_for_application(
            db,
            role_id=int(role.id),
            application=application,
        )
        if evaluation is None:
            return True
        return bool(
            str(evaluation.application_outcome or "open").strip().lower()
            != "open"
            or str(evaluation.pipeline_stage or "applied").strip().lower()
            == "advanced"
        )
    from ..domains.assessments_runtime.role_support import is_resolved

    return bool(is_resolved(application))


def assessment_uses_related_role_pipeline(db: Session, assessment) -> bool:
    """Whether an assessment must be kept off the source role's pipeline.

    A malformed cross-role assessment also fails closed here.  Falling back to
    the source application would mutate another role merely because the
    intended related role or membership became unavailable.
    """

    role_id = getattr(assessment, "role_id", None)
    application_id = getattr(assessment, "application_id", None)
    organization_id = getattr(assessment, "organization_id", None)
    if not role_id or not application_id or not organization_id:
        return False
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    application_role_id = (
        db.query(CandidateApplication.role_id)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .scalar()
    )
    if role is None:
        return application_role_id is not None
    if str(role.role_kind or "") == ROLE_KIND_SISTER:
        return True
    return bool(
        application_role_id is not None
        and int(application_role_id) != int(role_id)
    )


def lock_related_role_assessment_context(
    db: Session,
    *,
    assessment,
    lock_assessment: bool = False,
) -> RelatedRoleAssessmentContext:
    """Lock a related assessment in canonical lifecycle order.

    Locks are acquired as Organization -> ordered Roles -> logical source
    Application -> optional ATS transport Application -> Evaluation ->
    Assessment.  The ATS row is never execution authority for the local
    transition; locking it only establishes a stable linkage snapshot for
    callers that subsequently perform a provider handoff.
    """

    role_id = getattr(assessment, "role_id", None)
    application_id = getattr(assessment, "application_id", None)
    organization_id = getattr(assessment, "organization_id", None)
    if not role_id or not application_id or not organization_id:
        return RelatedRoleAssessmentContext(
            handled=False,
            reason="assessment_identity_incomplete",
        )

    def _lock_assessment_row() -> Assessment | None:
        if not lock_assessment or getattr(assessment, "id", None) is None:
            return None
        return (
            db.query(Assessment)
            .filter(
                Assessment.id == int(assessment.id),
                Assessment.organization_id == int(organization_id),
                Assessment.role_id == int(role_id),
                Assessment.application_id == int(application_id),
            )
            .with_for_update(of=Assessment)
            .populate_existing()
            .one_or_none()
        )

    role_identity = (
        db.query(Role.role_kind, Role.ats_owner_role_id, Role.deleted_at)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if role_identity is None or str(role_identity.role_kind or "") != ROLE_KIND_SISTER:
        # A role mismatch must never be interpreted as permission to mutate
        # the source application's owner-role pipeline.
        source_role_id = (
            db.query(CandidateApplication.role_id)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .scalar()
        )
        if role_identity is None:
            # Without a tenant-owned Role row there is no authority to classify
            # this as ordinary. Fail closed whenever the source application
            # exists, even if corrupt legacy data repeats the same id.
            cross_role = source_role_id is not None
        else:
            cross_role = bool(
                source_role_id is not None
                and int(source_role_id) != int(role_id)
            )
        return RelatedRoleAssessmentContext(
            handled=cross_role,
            reason=("assessment_role_unavailable" if cross_role else None),
            assessment=(_lock_assessment_row() if cross_role else None),
        )

    membership_identity = (
        db.query(
            SisterRoleEvaluation.id,
            SisterRoleEvaluation.ats_application_id,
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(organization_id),
            SisterRoleEvaluation.role_id == int(role_id),
            SisterRoleEvaluation.source_application_id == int(application_id),
        )
        .one_or_none()
    )
    membership_id = (
        int(membership_identity.id) if membership_identity is not None else None
    )
    ats_application_id = (
        int(membership_identity.ats_application_id)
        if membership_identity is not None
        and membership_identity.ats_application_id is not None
        else None
    )

    organization = (
        db.query(Organization.id)
        .filter(Organization.id == int(organization_id))
        .with_for_update(of=Organization)
        .scalar()
    )
    if organization is None:
        return RelatedRoleAssessmentContext(
            handled=True,
            reason="assessment_organization_unavailable",
            assessment=_lock_assessment_row(),
        )

    from .decision_membership import lock_resolution_roles

    role_ids = {int(role_id)}
    if role_identity.ats_owner_role_id is not None:
        role_ids.add(int(role_identity.ats_owner_role_id))
    locked_roles = lock_resolution_roles(
        db,
        organization_id=int(organization_id),
        role_ids=role_ids,
    )
    role = locked_roles.get(int(role_id))
    if role is None or str(role.role_kind or "") != ROLE_KIND_SISTER:
        return RelatedRoleAssessmentContext(
            handled=True,
            reason="assessment_role_unavailable",
            assessment=_lock_assessment_row(),
        )

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if application is None:
        return RelatedRoleAssessmentContext(
            handled=True,
            reason="assessment_application_unavailable",
            role=role,
            assessment=_lock_assessment_row(),
        )

    ats_application = None
    if ats_application_id is not None:
        ats_application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(ats_application_id),
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.candidate_id == int(application.candidate_id),
                CandidateApplication.role_id == role.ats_owner_role_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .with_for_update(of=CandidateApplication)
            .populate_existing()
            .one_or_none()
        )
    elif (
        role.ats_owner_role_id is not None
        and int(application.role_id) == int(role.ats_owner_role_id)
        and getattr(application, "deleted_at", None) is None
    ):
        # Mixed-version fallback: a pre-185 membership can use its source only
        # when that exact row already has the complete typed transport identity.
        ats_application = application

    evaluation = None
    if membership_id is not None:
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.id == int(membership_id),
                SisterRoleEvaluation.organization_id == int(organization_id),
                SisterRoleEvaluation.role_id == int(role_id),
                SisterRoleEvaluation.source_application_id == int(application.id),
                SisterRoleEvaluation.deleted_at.is_(None),
            )
            .with_for_update(of=SisterRoleEvaluation)
            .populate_existing()
            .one_or_none()
        )
    locked_assessment = None
    if lock_assessment:
        locked_assessment = _lock_assessment_row()
        if locked_assessment is None:
            return RelatedRoleAssessmentContext(
                handled=True,
                reason="assessment_unavailable",
                role=role,
                application=application,
                ats_application=ats_application,
                evaluation=evaluation,
            )

    if evaluation is None:
        reason = "related_membership_unavailable"
    elif (
        ats_application_id is not None
        and evaluation.ats_application_id != ats_application_id
    ):
        reason = "related_ats_link_changed"
    elif str(evaluation.application_outcome or "open").strip().lower() != "open":
        reason = "related_membership_resolved"
    elif str(evaluation.pipeline_stage or "applied").strip().lower() == "advanced":
        reason = "related_membership_advanced"
    else:
        reason = None
    return RelatedRoleAssessmentContext(
        handled=True,
        reason=reason,
        role=role,
        application=application,
        ats_application=ats_application,
        evaluation=evaluation,
        assessment=locked_assessment,
    )


def transition_related_role_assessment_stage(
    db: Session,
    *,
    assessment,
    to_stage: str,
    source: str,
    context: RelatedRoleAssessmentContext | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
    cleanup_decisions: bool = True,
) -> RelatedRoleAssessmentTransitionResult:
    """Apply one assessment-derived transition to its independent role.

    The return value distinguishes an ordinary assessment (``handled=False``)
    from a related assessment whose local transition was safely held.  That
    distinction prevents callers from ever falling through and changing the
    ATS owner's local pipeline after a membership is deleted or resolved.
    """

    context = context or lock_related_role_assessment_context(
        db,
        assessment=assessment,
    )
    if not context.handled:
        return RelatedRoleAssessmentTransitionResult(
            handled=False,
            reason=context.reason,
        )
    role_id = int(context.role.id) if context.role is not None else None
    evaluation_id = (
        int(context.evaluation.id) if context.evaluation is not None else None
    )
    event_key = str(idempotency_key or "").strip() or (
        f"related-assessment-stage:{getattr(assessment, 'id', 0)}:"
        f"{str(to_stage or '').strip().lower()}"
    )
    metadata = {
        "acting_role_id": role_id,
        "assessment_id": int(getattr(assessment, "id", 0) or 0),
        "assessment_status": str(
            getattr(getattr(assessment, "status", None), "value", None)
            or getattr(assessment, "status", "")
        ),
        "transition_source": source,
    }
    if context.reason is not None or context.application is None or role_id is None:
        if context.application is not None and role_id is not None:
            from ..domains.assessments_runtime.pipeline_service import (
                append_application_event,
            )

            append_application_event(
                db,
                app=context.application,
                role_id=role_id,
                event_type="role_pipeline_stage_transition_held",
                actor_type="system",
                reason=(
                    reason
                    or "Assessment stage was preserved because the role membership is no longer active"
                ),
                metadata={**metadata, "hold_reason": context.reason},
                target_stage=str(to_stage or "").strip().lower(),
                effect_status="held",
                idempotency_key=f"{event_key}:held",
            )
        return RelatedRoleAssessmentTransitionResult(
            handled=True,
            changed=False,
            reason=context.reason or "related_transition_unavailable",
            role_id=role_id,
            evaluation_id=evaluation_id,
        )

    from .pre_screen_decision_emitter import discard_pending_decisions_for_app
    from .related_role_action_service import transition_related_role_stage_action

    try:
        state = transition_related_role_stage_action(
            db,
            application=context.application,
            acting_role_id=role_id,
            to_stage=to_stage,
            source=source,
            actor_type="system",
            reason=reason or "Assessment changed this role's local stage",
            metadata=metadata,
            idempotency_key=event_key,
        )
    except HTTPException as exc:
        return RelatedRoleAssessmentTransitionResult(
            handled=True,
            changed=False,
            reason=f"transition_held:{exc.detail}",
            role_id=role_id,
            evaluation_id=evaluation_id,
        )
    assert state is not None
    # Any recommendation made before this assessment lifecycle transition is
    # stale.  Cleanup is exact to the acting role; owner and sibling cards are
    # independent and remain untouched.
    if cleanup_decisions or bool(state.changed):
        discard_pending_decisions_for_app(
            db,
            application_id=int(context.application.id),
            role_id=role_id,
            reason=(
                "superseded: assessment changed this role's candidate lifecycle"
            ),
            include_processing=True,
        )
    return RelatedRoleAssessmentTransitionResult(
        handled=True,
        changed=bool(state.changed),
        role_id=role_id,
        evaluation_id=int(state.evaluation.id),
    )


def advance_shared_application_family(
    db: Session,
    *,
    application: CandidateApplication,
    source: str,
) -> int:
    """Compatibility no-op: ATS movement never rewrites sibling role state."""

    return 0


def sync_shared_advance(
    db: Session,
    application: CandidateApplication,
    to_stage: str,
    source: str,
) -> int:
    """Compatibility no-op: shared ATS state is an action restriction only."""

    return 0


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
        # The physical application may belong to the ATS owner. Feed the score
        # builder a role-local view so its provenance, integrity and fallback
        # components cannot be inherited from that owner's score columns.
        role_local_score_application = SimpleNamespace(
            cv_match_details=(
                evaluation.details
                if evaluation is not None and isinstance(evaluation.details, dict)
                else {}
            ),
            cv_match_score=role_fit_score,
            cv_match_scored_at=(
                evaluation.scored_at if evaluation is not None else None
            ),
            cv_sections=getattr(application, "cv_sections", None),
            assessments=role_assessments,
        )
        summary = _score_summary_from_active_assessments(
            role_local_score_application, active
        )
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
            "scored_at": (
                evaluation.scored_at.isoformat()
                if evaluation is not None and evaluation.scored_at is not None
                else None
            ),
            "model": evaluation.model_version if evaluation is not None else None,
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
                AgentDecision.status.in_(
                    ("pending", "processing", "reverted_for_feedback")
                ),
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
    activity_candidates = [
        projected.get("last_activity_at"),
        *(
            value
            for assessment in role_assessments
            for value in (
                getattr(assessment, "updated_at", None),
                getattr(assessment, "completed_at", None),
                getattr(assessment, "created_at", None),
            )
        ),
        (
            pending.get("created_at")
            if isinstance(pending, dict)
            else getattr(pending, "created_at", None)
            if pending is not None
            else None
        ),
    ]
    comparable_activity = [
        value for value in activity_candidates if value is not None
    ]
    if comparable_activity:
        try:
            projected["last_activity_at"] = max(comparable_activity)
        except TypeError:
            # Mixed naive/aware historical timestamps should not make a
            # candidate projection unavailable. The evaluation timestamp set
            # by the base projection remains the safe role-local fallback.
            pass
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
    application_ids = [int(application.id) for application in applications]
    evaluation_map = {
        int(evaluation.source_application_id): evaluation
        for evaluation in db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.role_id == int(sister_role.id),
            SisterRoleEvaluation.source_application_id.in_(application_ids),
            SisterRoleEvaluation.deleted_at.is_(None),
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
        statuses=("pending", "processing", "reverted_for_feedback"),
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
    "RelatedRoleAssessmentContext",
    "RelatedRoleAssessmentTransitionResult",
    "advance_shared_application_family",
    "apply_related_role_runtime_projection",
    "assessment_uses_related_role_pipeline",
    "complete_timeout_pipeline",
    "project_related_role_page",
    "lock_related_role_assessment_context",
    "related_role_evaluation_for_application",
    "related_role_for_application",
    "role_application_is_resolved",
    "sync_shared_advance",
    "transition_related_role_assessment_stage",
]
