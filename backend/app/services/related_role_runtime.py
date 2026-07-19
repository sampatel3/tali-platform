"""Role-local decision runtime for coupled related roles.

Related roles share one ATS ``CandidateApplication`` with their owner, but
their score, assessment and Taali funnel belong to the related role.  This
module is the seam that keeps those two truths separate: it never manufactures
a second application and it never feeds a related role into the standard
cohort code, whose queries correctly assume ``application.role_id == role.id``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..actions import queue_decision
from ..actions.types import Actor
from ..agent_runtime.decision_translation import role_has_assessment_stage
from ..models.agent_decision import AgentDecision
from ..models.agent_run import AgentRun
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .agent_policy_settings import automation_enabled_for_decision
from .auto_threshold_service import resolve_role_fit_threshold
from .decision_role_context import (
    compact_requirements_from_details,
    integrity_from_evaluation,
    score_provenance_from_evaluation,
)
from .decision_staleness import criteria_content_fingerprint
from .role_execution_guard import automatic_role_action_block_reason
from .related_role_roster import related_source_application_is_live
from .related_role_runtime_batch import (
    INVITE_RETRYABLE_FAILURES,
    claim_related_role_runtime_batch,
)
from .sister_role_service import (
    source_application_is_globally_advanced,
    source_application_is_globally_closed,
    transition_related_role_stage,
)


_ASSESSMENT_TERMINAL = {
    AssessmentStatus.COMPLETED.value,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
}
_ASSESSMENT_ACTIVE = {
    AssessmentStatus.PENDING.value,
    AssessmentStatus.IN_PROGRESS.value,
}


def _status(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _numeric(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _assessment_score(assessment: Assessment) -> float | None:
    """Best persisted post-assessment headline in the platform's 0..100 scale."""

    for value in (
        assessment.taali_score,
        assessment.final_score,
        assessment.assessment_score,
    ):
        score = _numeric(value)
        if score is not None:
            return max(0.0, min(100.0, score))
    legacy = _numeric(assessment.score)
    if legacy is not None:
        return max(0.0, min(100.0, legacy * 10.0))
    return None


def _new_run(db: Session, *, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        trigger="cron",
        status="running",
        model_version="related-role-deterministic",
        prompt_version="related-role-runtime-v1",
    )
    db.add(run)
    db.flush()
    return run


def _queue_role_decision(
    db: Session,
    *,
    role: Role,
    evaluation: SisterRoleEvaluation,
    application: CandidateApplication,
    decision_type: str,
    score: float,
    threshold: float,
    assessment: Assessment | None,
    run: AgentRun,
) -> tuple[AgentDecision, bool]:
    app = application
    stage = "assessment" if assessment is not None else "full_scoring"
    requirements = compact_requirements_from_details(evaluation.details)
    evidence = {
        "decision_source": "policy",
        "decision_stage": stage,
        "source": "related_role_runtime",
        "related_role_id": int(role.id),
        "source_application_id": int(evaluation.source_application_id),
        "sister_evaluation_id": int(evaluation.id),
        "role_fit_score": float(evaluation.role_fit_score or 0.0),
        # The score that actually drove this decision. This is the role-fit
        # score before assessment and the role-owned assessment score after it.
        "taali_score": float(score),
        "effective_threshold": float(threshold),
        "shared_ats_application": True,
        "candidate_summary": evaluation.summary,
        "score_provenance": score_provenance_from_evaluation(evaluation),
        "requirements": requirements,
        "integrity": integrity_from_evaluation(evaluation, application=app),
        "evaluation_spec_fingerprint": evaluation.spec_fingerprint,
        "evaluation_cv_fingerprint": evaluation.cv_fingerprint,
    }
    if assessment is not None:
        evidence.update(
            {
                "assessment_id": int(assessment.id),
                "assessment_score": score,
                "assessment_invite_generation": int(
                    assessment.invite_email_send_generation or 0
                ),
                "task_id": (
                    int(assessment.task_id) if assessment.task_id is not None else None
                ),
            }
        )
    reasoning = (
        f"Related-role score {score:.0f} is below the {threshold:.0f} threshold; "
        "a recruiter must confirm rejection because the ATS application is shared."
        if decision_type == "reject"
        else (
            "This related role's assessment invite failed delivery; retry the existing invite."
            if decision_type == "resend_assessment_invite"
            else (
                f"Related-role score {score:.0f} meets the {threshold:.0f} threshold; "
                "send this role's assessment."
                if decision_type == "send_assessment"
                else f"Related-role score {score:.0f} meets the {threshold:.0f} threshold; advance the shared application."
            )
        )
    )
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type=decision_type,
        reasoning=reasoning,
        evidence=evidence,
        confidence=1.0,
        model_version="related-role-deterministic",
        prompt_version="related-role-runtime-v1",
        recommendation=decision_type,
        skip_episode=True,
    )
    created = bool(getattr(decision, "_just_created", True))
    return decision, created


def _maybe_execute_positive(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
    decision_type: str,
) -> bool:
    """Run only the reversible positive actions granted by this role."""

    if decision_type == "reject" or not automation_enabled_for_decision(
        role, decision_type
    ):
        return False
    from ..agent_runtime.tool_registry import maybe_auto_execute_decision

    result = maybe_auto_execute_decision(
        db,
        role=role,
        decision=decision,
        decision_type=decision_type,
        on_policy=True,
        force_human_review=False,
    )
    if not bool(result.get("executed")):
        return False
    return True


def run_related_role_cycle(
    db: Session,
    *,
    role: Role,
    evaluation_id: int | None = None,
    limit: int = 250,
) -> dict:
    """Materialise decisions/actions for one related role's local funnel."""

    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise ValueError("Role is not a coupled related role")
    block_reason = automatic_role_action_block_reason(role, db=db)
    if block_reason:
        return {
            "status": "skipped",
            "reason": block_reason,
            "role_id": int(role.id),
        }

    resolved_threshold = resolve_role_fit_threshold(db, role=role)
    threshold = float(
        resolved_threshold
        if resolved_threshold is not None
        else (role.score_threshold if role.score_threshold is not None else 50)
    )
    has_assessment = role_has_assessment_stage(role)
    criteria_fingerprint = criteria_content_fingerprint(db, int(role.id))

    # Claim every canonical application in id order before any role-local
    # evaluation. Sibling related roles share those application rows, so this
    # application -> evaluation order prevents cross-role deadlocks. Decision
    # and assessment state is read once for the whole bounded batch.
    batch = claim_related_role_runtime_batch(
        db,
        role=role,
        evaluation_id=evaluation_id,
        limit=limit,
        threshold=threshold,
        has_assessment_stage=has_assessment,
        criteria_fingerprint=criteria_fingerprint,
    )
    summary: Counter = Counter()
    if batch.locked:
        summary["locked"] = int(batch.locked)
    run: AgentRun | None = None

    for evaluation in batch.evaluations:
        application_id = int(evaluation.source_application_id)
        app = batch.applications.get(application_id)
        if not related_source_application_is_live(role, app):
            summary["outside_roster"] += 1
            continue
        if source_application_is_globally_closed(app):
            summary["closed"] += 1
            continue
        if source_application_is_globally_advanced(app):
            transition_related_role_stage(
                evaluation, to_stage="advanced", source="system"
            )
            summary["advanced"] += 1
            continue
        existing = batch.pending_decisions.get(application_id)
        if existing is not None:
            summary["pending"] += 1
            continue

        assessment = batch.assessments.get(application_id)
        assessment_status = _status(assessment.status) if assessment is not None else ""
        if assessment is not None and assessment_status in _ASSESSMENT_ACTIVE:
            if assessment_status == AssessmentStatus.IN_PROGRESS.value:
                transition_related_role_stage(
                    evaluation,
                    to_stage="in_assessment",
                    source="system",
                )
            elif assessment.invite_sent_at is not None:
                transition_related_role_stage(
                    evaluation,
                    to_stage="invited",
                    source="system",
                )
            if (
                str(assessment.invite_email_status or "").strip().lower()
                in INVITE_RETRYABLE_FAILURES
            ):
                score = _numeric(evaluation.role_fit_score) or 0.0
                run = run or _new_run(db, role=role)
                decision, created = _queue_role_decision(
                    db,
                    role=role,
                    evaluation=evaluation,
                    application=app,
                    decision_type="resend_assessment_invite",
                    score=float(score),
                    threshold=threshold,
                    assessment=assessment,
                    run=run,
                )
                summary["created" if created else "deduplicated"] += 1
                summary["resend_assessment_invite"] += 1
                if created and _maybe_execute_positive(
                    db,
                    role=role,
                    decision=decision,
                    decision_type="resend_assessment_invite",
                ):
                    summary["auto_executed"] += 1
            summary["assessment_active"] += 1
            continue

        if (
            assessment is not None
            and assessment_status == AssessmentStatus.EXPIRED.value
        ):
            score = _numeric(evaluation.role_fit_score) or 0.0
            decision_type = "resend_assessment_invite"
        elif assessment is not None and assessment_status in _ASSESSMENT_TERMINAL:
            score = _assessment_score(assessment)
            if score is None or bool(
                assessment.scoring_failed or assessment.scoring_partial
            ):
                summary["assessment_incomplete"] += 1
                continue
            transition_related_role_stage(
                evaluation, to_stage="review", source="system"
            )
            decision_type = "advance_to_interview" if score >= threshold else "reject"
        else:
            score = _numeric(evaluation.role_fit_score)
            if score is None:
                summary["missing_score"] += 1
                continue
            if score < threshold:
                decision_type = "reject"
            elif has_assessment:
                decision_type = "send_assessment"
            else:
                decision_type = "advance_to_interview"

        run = run or _new_run(db, role=role)
        decision, created = _queue_role_decision(
            db,
            role=role,
            evaluation=evaluation,
            application=app,
            decision_type=decision_type,
            score=float(score),
            threshold=threshold,
            assessment=assessment,
            run=run,
        )
        summary["created" if created else "deduplicated"] += 1
        summary[decision_type] += 1
        if created and _maybe_execute_positive(
            db,
            role=role,
            decision=decision,
            decision_type=decision_type,
        ):
            summary["auto_executed"] += 1

    if run is not None:
        run.status = "succeeded"
        run.decisions_emitted = int(summary["created"])
        run.finished_at = datetime.now(timezone.utc)
        role.agent_last_run_at = run.finished_at
        role.agent_bootstrap_status = "ready"
        role.agent_bootstrap_error = None
        role.agent_bootstrap_completed_at = run.finished_at
    db.commit()
    result = {"status": "ok", "role_id": int(role.id), **dict(summary)}
    if batch.has_more or batch.locked:
        result["has_more"] = True
    return result


__all__ = ["run_related_role_cycle"]
