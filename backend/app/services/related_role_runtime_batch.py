"""Bounded batch claiming for the related-role decision runtime."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, aliased, joinedload

from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import SISTER_EVAL_DONE, SisterRoleEvaluation
from .related_role_decision_suppression import (
    related_role_decision_is_suppressed,
)

INVITE_RETRYABLE_FAILURES = frozenset(
    {"bounced", "complained", "failed", "dispatch_failed"}
)


@dataclass(frozen=True)
class RelatedRoleRuntimeBatch:
    evaluations: tuple[SisterRoleEvaluation, ...]
    applications: dict[int, CandidateApplication]
    pending_decisions: dict[int, AgentDecision]
    assessments: dict[int, Assessment]
    locked: int = 0
    has_more: bool = False


def _role_wide_actionable_query(
    db: Session,
    *,
    role: Role,
    threshold: float,
    has_assessment_stage: bool,
    criteria_fingerprint: str | None,
):
    source_application = aliased(
        CandidateApplication, name="related_runtime_source_application"
    )
    source_candidate = aliased(Candidate, name="related_runtime_source_candidate")
    owner_role = aliased(Role, name="related_runtime_owner_role")
    current_assessment = aliased(Assessment, name="related_runtime_current_assessment")
    query = (
        db.query(
            SisterRoleEvaluation.id,
            SisterRoleEvaluation.source_application_id,
        )
        .join(
            source_application,
            source_application.id == SisterRoleEvaluation.source_application_id,
        )
        .join(
            source_candidate,
            source_candidate.id == source_application.candidate_id,
        )
        .join(owner_role, owner_role.id == source_application.role_id)
        .outerjoin(
            current_assessment,
            and_(
                current_assessment.organization_id == int(role.organization_id),
                current_assessment.role_id == int(role.id),
                current_assessment.application_id
                == SisterRoleEvaluation.source_application_id,
                current_assessment.candidate_id == source_candidate.id,
                current_assessment.is_voided.is_(False),
            ),
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            source_application.organization_id == int(role.organization_id),
            source_application.role_id == int(role.ats_owner_role_id),
            source_application.deleted_at.is_(None),
            or_(
                source_application.application_outcome.is_(None),
                source_application.application_outcome == "open",
            ),
            source_application.workable_disqualified.is_not(True),
            source_candidate.organization_id == int(role.organization_id),
            source_candidate.deleted_at.is_(None),
            owner_role.organization_id == int(role.organization_id),
            owner_role.role_kind == ROLE_KIND_STANDARD,
            owner_role.ats_owner_role_id.is_(None),
            owner_role.deleted_at.is_(None),
        )
    )
    pending_decision = (
        db.query(AgentDecision.id)
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == SisterRoleEvaluation.source_application_id,
            AgentDecision.status.in_(("pending", "processing")),
        )
        .exists()
    )
    normalized_invite_status = func.lower(
        func.trim(func.coalesce(current_assessment.invite_email_status, ""))
    )
    assessment_terminal = current_assessment.status.in_(
        (
            AssessmentStatus.COMPLETED,
            AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        )
    )
    raw_assessment_score = case(
        (current_assessment.taali_score.is_not(None), current_assessment.taali_score),
        (current_assessment.final_score.is_not(None), current_assessment.final_score),
        (
            current_assessment.assessment_score.is_not(None),
            current_assessment.assessment_score,
        ),
        (current_assessment.score.is_not(None), current_assessment.score * 10.0),
        else_=None,
    )
    assessment_score = case(
        (raw_assessment_score < 0.0, 0.0),
        (raw_assessment_score > 100.0, 100.0),
        else_=raw_assessment_score,
    )
    valid_terminal_assessment = and_(
        assessment_terminal,
        current_assessment.scoring_failed.is_not(True),
        current_assessment.scoring_partial.is_not(True),
        assessment_score.is_not(None),
    )
    retryable_active_assessment = and_(
        current_assessment.status.in_(
            (AssessmentStatus.PENDING, AssessmentStatus.IN_PROGRESS)
        ),
        normalized_invite_status.in_(INVITE_RETRYABLE_FAILURES),
    )
    assessment_decision_needed = or_(
        current_assessment.status == AssessmentStatus.EXPIRED,
        valid_terminal_assessment,
        retryable_active_assessment,
    )
    assessment_projection_needed = or_(
        and_(
            current_assessment.status == AssessmentStatus.PENDING,
            current_assessment.invite_sent_at.is_not(None),
            SisterRoleEvaluation.pipeline_stage.notin_(("invited", "advanced")),
        ),
        and_(
            current_assessment.status == AssessmentStatus.IN_PROGRESS,
            SisterRoleEvaluation.pipeline_stage.notin_(("in_assessment", "advanced")),
        ),
    )
    expected_decision_type = case(
        (
            or_(
                current_assessment.status == AssessmentStatus.EXPIRED,
                retryable_active_assessment,
            ),
            "resend_assessment_invite",
        ),
        (and_(valid_terminal_assessment, assessment_score < threshold), "reject"),
        (valid_terminal_assessment, "advance_to_interview"),
        (SisterRoleEvaluation.role_fit_score < threshold, "reject"),
        (
            and_(
                current_assessment.id.is_(None),
                bool(has_assessment_stage),
            ),
            "send_assessment",
        ),
        else_="advance_to_interview",
    )
    current_decision_score = case(
        (valid_terminal_assessment, assessment_score),
        else_=SisterRoleEvaluation.role_fit_score,
    )
    handled_current_generation = related_role_decision_is_suppressed(
        db,
        role=role,
        threshold=threshold,
        criteria_fingerprint=criteria_fingerprint,
        expected_decision_type=expected_decision_type,
        current_decision_score=current_decision_score,
        current_assessment=current_assessment,
        current_assessment_score=assessment_score,
    )
    assessment_actionable = or_(
        assessment_projection_needed,
        and_(assessment_decision_needed, ~handled_current_generation),
    )
    has_assessment = current_assessment.id.is_not(None)
    application_advanced = (
        func.lower(func.trim(func.coalesce(source_application.pipeline_stage, "")))
        == "advanced"
    )
    evaluation_advanced = (
        func.lower(func.trim(func.coalesce(SisterRoleEvaluation.pipeline_stage, "")))
        == "advanced"
    )
    return query.filter(
        or_(
            and_(application_advanced, ~evaluation_advanced),
            and_(
                ~application_advanced,
                ~pending_decision,
                or_(
                    assessment_actionable,
                    and_(
                        ~has_assessment,
                        SisterRoleEvaluation.role_fit_score.is_not(None),
                        ~handled_current_generation,
                    ),
                ),
            ),
        )
    )


def _candidate_rows(
    db: Session,
    *,
    role: Role,
    evaluation_id: int | None,
    limit: int,
    threshold: float,
    has_assessment_stage: bool,
    criteria_fingerprint: str | None,
) -> tuple[list[tuple[int, int]], bool]:
    bounded_limit = max(1, int(limit))
    if evaluation_id is None:
        query = _role_wide_actionable_query(
            db,
            role=role,
            threshold=threshold,
            has_assessment_stage=has_assessment_stage,
            criteria_fingerprint=criteria_fingerprint,
        )
    else:
        query = db.query(
            SisterRoleEvaluation.id,
            SisterRoleEvaluation.source_application_id,
        ).filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            SisterRoleEvaluation.id == int(evaluation_id),
        )
    rows = [
        (int(row_id), int(application_id))
        for row_id, application_id in query.order_by(SisterRoleEvaluation.id.asc())
        .limit(bounded_limit + 1)
        .all()
    ]
    return rows[:bounded_limit], len(rows) > bounded_limit


def claim_related_role_runtime_batch(
    db: Session,
    *,
    role: Role,
    evaluation_id: int | None,
    limit: int,
    threshold: float,
    has_assessment_stage: bool,
    criteria_fingerprint: str | None,
) -> RelatedRoleRuntimeBatch:
    """Claim one bounded batch in application-then-evaluation lock order."""

    candidates, has_more = _candidate_rows(
        db,
        role=role,
        evaluation_id=evaluation_id,
        limit=limit,
        threshold=threshold,
        has_assessment_stage=has_assessment_stage,
        criteria_fingerprint=criteria_fingerprint,
    )
    if not candidates:
        return RelatedRoleRuntimeBatch((), {}, {}, {}, has_more=has_more)

    selected_application_ids = sorted(
        {application_id for _, application_id in candidates}
    )
    locked_applications = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id.in_(selected_application_ids),
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .order_by(CandidateApplication.id.asc())
        .populate_existing()
        .with_for_update(of=CandidateApplication, skip_locked=True)
        .all()
    )
    applications = {
        int(application.id): application for application in locked_applications
    }
    selected_evaluation_ids = [
        row_id
        for row_id, application_id in candidates
        if application_id in applications
    ]
    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.id.in_(selected_evaluation_ids),
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            SisterRoleEvaluation.source_application_id.in_(applications),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .populate_existing()
        .with_for_update(of=SisterRoleEvaluation, skip_locked=True)
        .all()
        if selected_evaluation_ids
        else []
    )
    claimed_application_ids = {
        int(evaluation.source_application_id) for evaluation in evaluations
    }

    pending_decisions: dict[int, AgentDecision] = {}
    assessments: dict[int, Assessment] = {}
    if claimed_application_ids:
        decision_rows = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.organization_id == int(role.organization_id),
                AgentDecision.role_id == int(role.id),
                AgentDecision.application_id.in_(claimed_application_ids),
                AgentDecision.status.in_(("pending", "processing")),
            )
            .order_by(AgentDecision.application_id.asc(), AgentDecision.id.desc())
            .all()
        )
        for decision in decision_rows:
            pending_decisions.setdefault(int(decision.application_id), decision)

        assessment_rows = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id == int(role.organization_id),
                Assessment.role_id == int(role.id),
                Assessment.application_id.in_(claimed_application_ids),
                Assessment.is_voided.is_(False),
            )
            .order_by(
                Assessment.application_id.asc(),
                Assessment.created_at.desc(),
                Assessment.id.desc(),
            )
            .all()
        )
        for assessment in assessment_rows:
            app = applications.get(int(assessment.application_id))
            if app is not None and int(assessment.candidate_id) == int(
                app.candidate_id
            ):
                assessments.setdefault(int(assessment.application_id), assessment)

    return RelatedRoleRuntimeBatch(
        tuple(evaluations),
        applications,
        pending_decisions,
        assessments,
        locked=len(candidates) - len(evaluations),
        has_more=has_more,
    )


__all__ = [
    "INVITE_RETRYABLE_FAILURES",
    "RelatedRoleRuntimeBatch",
    "claim_related_role_runtime_batch",
]
