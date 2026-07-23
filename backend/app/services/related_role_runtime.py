"""Role-local decision runtime for independent related roles.

A membership may reuse an application as candidate evidence and optional ATS
transport. Its score, assessment, funnel, outcome, decisions, and history all
belong to the related role. This module never infers membership or state from
the transport row and never feeds a related role into standard cohort code,
whose queries correctly assume ``application.role_id == role.id``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from ..actions import queue_decision
from ..actions.types import Actor
from ..models.agent_decision import AgentDecision
from ..models.agent_run import AgentRun
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SISTER_EVAL_DONE, SisterRoleEvaluation
from .agent_policy_settings import automation_enabled_for_decision
from .decision_role_context import (
    compact_requirements_from_details,
    integrity_from_evaluation,
    score_provenance_from_evaluation,
)
from .decision_policy_generation import (
    DecisionPolicyGeneration,
    capture_decision_policy_generation,
)
from .role_execution_guard import automatic_role_action_block_reason, lock_live_role


_ASSESSMENT_TERMINAL = {
    AssessmentStatus.COMPLETED.value,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
}
_ASSESSMENT_ACTIVE = {
    AssessmentStatus.PENDING.value,
    AssessmentStatus.IN_PROGRESS.value,
}
_INVITE_RETRYABLE_FAILURES = {"bounced", "complained", "failed", "dispatch_failed"}


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


def _latest_assessment(
    db: Session, *, role: Role, evaluation: SisterRoleEvaluation
) -> Assessment | None:
    app = evaluation.source_application
    if app is None:
        return None
    return (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == int(role.organization_id),
            Assessment.application_id == int(app.id),
            Assessment.candidate_id == int(app.candidate_id),
            Assessment.role_id == int(role.id),
            Assessment.is_voided.is_(False),
        )
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )


def _pending_decision(
    db: Session, *, role: Role, evaluation: SisterRoleEvaluation
) -> AgentDecision | None:
    return (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == int(evaluation.source_application_id),
            AgentDecision.status.in_(
                ("pending", "processing", "reverted_for_feedback")
            ),
        )
        .order_by(AgentDecision.id.desc())
        .first()
    )


def _stored_threshold(decision: AgentDecision) -> float | None:
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    value = evidence.get("effective_threshold")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _discard_superseded_threshold_decisions(
    db: Session,
    *,
    role: Role,
    threshold: float,
) -> Counter:
    """Retire only pending related-runtime cards from an older boundary.

    Processing decisions are an acknowledged in-flight recruiter/automation
    action and are never rewritten here. Resolved decisions remain immutable
    history. Unknown/manual pending cards are also preserved; only decisions
    with explicit related-runtime policy provenance are safe to regenerate.
    Membership is joined role-locally, so owner application state cannot enter
    the reconciliation.
    """

    rows = (
        db.query(AgentDecision)
        .join(
            SisterRoleEvaluation,
            (SisterRoleEvaluation.organization_id == AgentDecision.organization_id)
            & (SisterRoleEvaluation.role_id == AgentDecision.role_id)
            & (
                SisterRoleEvaluation.source_application_id
                == AgentDecision.application_id
            ),
        )
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    summary: Counter = Counter()
    for decision in rows:
        evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
        if (
            evidence.get("decision_source") != "policy"
            or evidence.get("source") != "related_role_runtime"
            or evidence.get("related_role_id") != int(role.id)
        ):
            continue
        previous = _stored_threshold(decision)
        if previous is not None and abs(previous - float(threshold)) < 0.05:
            continue
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = (
            "superseded: related-role threshold changed to "
            f"{float(threshold):.1f}"
        )[:500]
        summary["threshold_discarded"] += 1
        if decision.decision_type in {
            "advance_to_interview",
            "send_assessment",
            "resend_assessment_invite",
        }:
            summary["threshold_discarded_advances"] += 1
    if summary["threshold_discarded"]:
        # Release the one-active-decision slot before queue_decision attempts
        # to materialise the current role-local verdict.
        db.commit()
    return summary


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
    decision_type: str,
    score: float,
    threshold: float,
    assessment: Assessment | None,
    run: AgentRun,
    policy_generation: DecisionPolicyGeneration,
) -> tuple[AgentDecision, bool]:
    app = evaluation.source_application
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
        "has_assessment_task": policy_generation.has_assessment_task,
        "policy_revision_id": policy_generation.policy_revision_id,
        "ats_transport_linked": evaluation.ats_application_id is not None,
        "role_state_is_independent": True,
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
                "task_id": int(assessment.task_id),
            }
        )
    reasoning = (
        f"Related-role score {score:.0f} is below the {threshold:.0f} threshold; "
        "reject this candidate only in this role."
        if decision_type == "reject"
        else (
            "This related role's assessment invite failed delivery; retry the existing invite."
            if decision_type == "resend_assessment_invite"
            else (
                f"Related-role score {score:.0f} meets the {threshold:.0f} threshold; "
                "send this role's assessment."
                if decision_type == "send_assessment"
                else f"Related-role score {score:.0f} meets the {threshold:.0f} threshold; advance this role's candidate."
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


def _maybe_execute_automatic(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
    decision_type: str,
) -> bool:
    """Run the exact deterministic actions granted by this role's settings."""

    if not automation_enabled_for_decision(role, decision_type):
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

    role_id = int(role.id)
    organization_id = int(role.organization_id)
    # Queueing can immediately auto-execute. Establish the platform-wide lock
    # order before any shared Application or role-local evaluation lock so that
    # the later side-effect boundary cannot invert Application -> Org/Role.
    locked_role = lock_live_role(
        db,
        role_id=role_id,
        organization_id=organization_id,
    )
    if locked_role is None:
        return {
            "status": "skipped",
            "reason": "role is unavailable",
            "role_id": role_id,
        }
    # The caller commonly loaded Role before entering this service. Finish that
    # transaction while only the canonical Org/Role pair is held, then reacquire
    # the live pair in a fresh transaction before any candidate-row claim.
    db.commit()
    locked_role = lock_live_role(
        db,
        role_id=role_id,
        organization_id=organization_id,
    )
    if locked_role is None:
        return {
            "status": "skipped",
            "reason": "role is unavailable",
            "role_id": role_id,
        }
    role = locked_role
    if str(role.role_kind or "") != ROLE_KIND_SISTER:
        raise ValueError("Role is not a related role")
    block_reason = automatic_role_action_block_reason(role, db=db)
    if block_reason:
        return {
            "status": "skipped",
            "reason": block_reason,
            "role_id": role_id,
        }

    query = (
        db.query(SisterRoleEvaluation.id)
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            SisterRoleEvaluation.deleted_at.is_(None),
            SisterRoleEvaluation.application_outcome == "open",
            SisterRoleEvaluation.pipeline_stage != "advanced",
        )
    )
    if evaluation_id is not None:
        query = query.filter(SisterRoleEvaluation.id == int(evaluation_id))
    rows = (
        query.order_by(SisterRoleEvaluation.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    evaluation_ids = [int(row_id) for (row_id,) in rows]
    summary: Counter = Counter()
    run: AgentRun | None = None
    automatic_decisions: list[tuple[int, str]] = []
    policy_generation = capture_decision_policy_generation(db, role=role)
    resolved_threshold = policy_generation.effective_threshold
    threshold = float(
        resolved_threshold
        if resolved_threshold is not None
        else (role.score_threshold if role.score_threshold is not None else 50)
    )
    has_assessment = policy_generation.has_assessment_task
    summary.update(
        _discard_superseded_threshold_decisions(
            db,
            role=role,
            threshold=threshold,
        )
    )

    for current_evaluation_id in evaluation_ids:
        locator = (
            db.query(
                SisterRoleEvaluation.source_application_id,
                SisterRoleEvaluation.ats_application_id,
            )
            .filter(SisterRoleEvaluation.id == current_evaluation_id)
            .one_or_none()
        )
        if locator is None:
            continue
        # Lock source evidence before role-local state so concurrent scoring and
        # decision cycles use a consistent candidate snapshot.
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(locator[0]),
                CandidateApplication.organization_id == int(role.organization_id),
            )
            .with_for_update(skip_locked=True)
            .one_or_none()
        )
        if app is None:
            summary["locked"] += 1
            continue
        ats_application_id = int(locator[1]) if locator[1] is not None else None
        if ats_application_id is not None and ats_application_id != int(app.id):
            # The ATS row is transport/restriction state only. Lock it after
            # the logical application to stabilize this cycle's linkage, but
            # do not require it to exist in order to decide this role locally.
            (
                db.query(CandidateApplication.id)
                .filter(
                    CandidateApplication.id == ats_application_id,
                    CandidateApplication.organization_id
                    == int(role.organization_id),
                )
                .with_for_update(of=CandidateApplication, skip_locked=True)
                .scalar()
            )
        evaluation = (
            db.query(SisterRoleEvaluation)
            .options(joinedload(SisterRoleEvaluation.source_application))
            .filter(
                SisterRoleEvaluation.id == current_evaluation_id,
                SisterRoleEvaluation.role_id == int(role.id),
                SisterRoleEvaluation.status == SISTER_EVAL_DONE,
                SisterRoleEvaluation.source_application_id == int(app.id),
                SisterRoleEvaluation.deleted_at.is_(None),
                SisterRoleEvaluation.application_outcome == "open",
                SisterRoleEvaluation.pipeline_stage != "advanced",
            )
            .with_for_update(of=SisterRoleEvaluation, skip_locked=True)
            .one_or_none()
        )
        if evaluation is None:
            summary["locked"] += 1
            continue
        app = evaluation.source_application
        if app is None:
            summary["missing_source"] += 1
            continue

        assessment = _latest_assessment(db, role=role, evaluation=evaluation)
        assessment_status = _status(assessment.status) if assessment is not None else ""
        if assessment is not None and assessment_status in _ASSESSMENT_ACTIVE:
            if assessment_status == AssessmentStatus.IN_PROGRESS.value:
                target_stage = "in_assessment"
            elif assessment.invite_sent_at is not None:
                target_stage = "invited"
            else:
                target_stage = None
            if target_stage is not None:
                from .related_role_application_runtime import (
                    RelatedRoleAssessmentContext,
                    transition_related_role_assessment_stage,
                )

                transition_related_role_assessment_stage(
                    db,
                    assessment=assessment,
                    to_stage=target_stage,
                    source="system",
                    context=RelatedRoleAssessmentContext(
                        handled=True,
                        role=role,
                        application=app,
                        evaluation=evaluation,
                        assessment=assessment,
                    ),
                    idempotency_key=(
                        f"related-runtime-assessment-stage:{assessment.id}:"
                        f"{target_stage}"
                    ),
                    reason="Related-role assessment lifecycle reconciled",
                    cleanup_decisions=False,
                )
            existing = _pending_decision(db, role=role, evaluation=evaluation)
            if existing is not None:
                summary["pending"] += 1
                summary["assessment_active"] += 1
                continue
            if str(assessment.invite_email_status or "").strip().lower() in _INVITE_RETRYABLE_FAILURES:
                score = _numeric(evaluation.role_fit_score) or 0.0
                run = run or _new_run(db, role=role)
                decision, created = _queue_role_decision(
                    db,
                    role=role,
                    evaluation=evaluation,
                    decision_type="resend_assessment_invite",
                    score=float(score),
                    threshold=threshold,
                    assessment=assessment,
                    run=run,
                    policy_generation=policy_generation,
                )
                summary["created" if created else "deduplicated"] += 1
                summary["resend_assessment_invite"] += 1
                if created and automation_enabled_for_decision(
                    role, "resend_assessment_invite"
                ):
                    automatic_decisions.append(
                        (int(decision.id), "resend_assessment_invite")
                    )
            summary["assessment_active"] += 1
            continue

        if assessment is not None and assessment_status in _ASSESSMENT_TERMINAL:
            from .related_role_application_runtime import (
                RelatedRoleAssessmentContext,
                transition_related_role_assessment_stage,
            )

            transition_related_role_assessment_stage(
                db,
                assessment=assessment,
                to_stage="review",
                source="system",
                context=RelatedRoleAssessmentContext(
                    handled=True,
                    role=role,
                    application=app,
                    evaluation=evaluation,
                    assessment=assessment,
                ),
                idempotency_key=(
                    f"related-runtime-assessment-stage:{assessment.id}:review"
                ),
                reason="Related-role assessment completion reconciled",
                cleanup_decisions=False,
            )

        # Re-read the membership after canonical stage reconciliation. A
        # concurrent close/delete/advance wins and prevents any new decision.
        db.refresh(evaluation)
        if (
            evaluation.deleted_at is not None
            or str(evaluation.application_outcome or "open").strip().lower()
            != "open"
            or str(evaluation.pipeline_stage or "applied").strip().lower()
            == "advanced"
        ):
            summary["resolved"] += 1
            continue

        existing = _pending_decision(db, role=role, evaluation=evaluation)
        if existing is not None:
            summary["pending"] += 1
            continue

        if assessment is not None and assessment_status == AssessmentStatus.EXPIRED.value:
            score = _numeric(evaluation.role_fit_score) or 0.0
            decision_type = "resend_assessment_invite"
        elif assessment is not None and assessment_status in _ASSESSMENT_TERMINAL:
            score = _assessment_score(assessment)
            if score is None or bool(assessment.scoring_failed or assessment.scoring_partial):
                summary["assessment_incomplete"] += 1
                continue
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
            decision_type=decision_type,
            score=float(score),
            threshold=threshold,
            assessment=assessment,
            run=run,
            policy_generation=policy_generation,
        )
        summary["created" if created else "deduplicated"] += 1
        summary[decision_type] += 1
        if created and automation_enabled_for_decision(role, decision_type):
            automatic_decisions.append((int(decision.id), decision_type))

    if run is not None:
        run.status = "succeeded"
        run.decisions_emitted = int(summary["created"])
        run.finished_at = datetime.now(timezone.utc)
        role.agent_last_run_at = run.finished_at
        role.agent_bootstrap_status = "ready"
        role.agent_bootstrap_error = None
        role.agent_bootstrap_completed_at = run.finished_at
    # Release membership locks before any automatic action can reach an email
    # or ATS provider. The action boundary re-locks and rechecks every row.
    db.commit()
    for decision_id, decision_type in automatic_decisions:
        live_role = (
            db.query(Role)
            .filter(
                Role.id == role_id,
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
            )
            .one_or_none()
        )
        live_decision = db.get(AgentDecision, int(decision_id))
        if live_role is None or live_decision is None:
            continue
        if _maybe_execute_automatic(
            db,
            role=live_role,
            decision=live_decision,
            decision_type=decision_type,
        ):
            summary["auto_executed"] += 1
        db.commit()
    return {"status": "ok", "role_id": role_id, **dict(summary)}


__all__ = ["run_related_role_cycle"]
