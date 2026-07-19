"""Freshness checks for decisions over shared ATS applications."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from ..cv_matching.holistic import is_engine_outdated, resolve_engine_version
from ..domains.assessments_runtime.role_support import is_resolved
from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import SISTER_EVAL_DONE, SisterRoleEvaluation
from .auto_threshold_service import resolve_role_fit_threshold
from .decision_staleness import (
    SCORE_DRIFT_BAND,
    StalenessCache,
    StalenessReport,
    _latest_recruiter_note_id,
    criteria_content_fingerprint,
)


_ASSESSMENT_NOT_LOADED = object()
_ASSESSMENT_TERMINAL_STATUSES = {
    AssessmentStatus.COMPLETED.value,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
}


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_score(*candidates: Any) -> float | None:
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            score = float(candidate)
        except (TypeError, ValueError):
            continue
        if score == score and score not in (float("inf"), float("-inf")):
            return score
    return None


def _status(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _text_fingerprint(value: object) -> str | None:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def _application_cv_text(application: CandidateApplication | None) -> str:
    if application is None:
        return ""
    text = str(getattr(application, "cv_text", None) or "").strip()
    if text:
        return text
    candidate = getattr(application, "candidate", None)
    return str(getattr(candidate, "cv_text", None) or "").strip()


def _assessment_score(assessment: Assessment | None) -> float | None:
    if assessment is None:
        return None
    for value in (
        assessment.taali_score,
        assessment.final_score,
        assessment.assessment_score,
    ):
        score = _first_score(value)
        if score is not None:
            return max(0.0, min(100.0, score))
    legacy = _first_score(assessment.score)
    if legacy is not None:
        return max(0.0, min(100.0, legacy * 10.0))
    return None


def related_decision_staleness(
    db: Session,
    decision: AgentDecision,
    evaluation: SisterRoleEvaluation | None,
    *,
    application: CandidateApplication | None = None,
    role: Role | None = None,
    cache: StalenessCache | None = None,
    assessment: Assessment | None | object = _ASSESSMENT_NOT_LOADED,
) -> StalenessReport:
    """Freshness check that never consults the owner role's score columns."""

    if application is None:
        application = db.get(CandidateApplication, int(decision.application_id))
    if application is not None and is_resolved(application):
        return StalenessReport(is_stale=False)
    if role is None:
        role = (
            db.query(Role)
            .filter(
                Role.id == int(decision.role_id),
                Role.organization_id == int(decision.organization_id),
            )
            .one_or_none()
        )
    if evaluation is None:
        return StalenessReport(
            is_stale=True,
            reasons=["related_role_evaluation_missing"],
            summary="This role's evaluation is unavailable. Re-evaluate before deciding.",
        )
    status = str(evaluation.status or "")
    if status != SISTER_EVAL_DONE:
        return StalenessReport(
            is_stale=True,
            reasons=["related_role_evaluation_incomplete"],
            summary="This role's evaluation is being refreshed. Wait before deciding.",
            details={"evaluation_status": status},
        )

    reasons: list[str] = []
    details: dict[str, object] = {}
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    fingerprint = (
        decision.input_fingerprint
        if isinstance(decision.input_fingerprint, dict)
        else {}
    )

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    snapshotted_id = evidence.get("sister_evaluation_id")
    parsed_snapshot_id = _safe_int(snapshotted_id)
    if snapshotted_id is not None and (
        parsed_snapshot_id is None or parsed_snapshot_id != int(evaluation.id)
    ):
        add_reason("related_role_evaluation_changed")

    for evidence_key, current, reason in (
        (
            "evaluation_spec_fingerprint",
            evaluation.spec_fingerprint,
            "criteria_changed",
        ),
        (
            "evaluation_cv_fingerprint",
            evaluation.cv_fingerprint,
            "cv_replaced",
        ),
    ):
        snapshotted = evidence.get(evidence_key)
        if snapshotted and current and str(snapshotted) != str(current):
            add_reason(reason)

    # Compare the frozen evaluation inputs to live shared candidate / named-role
    # inputs too. The evaluation row may not have been reset yet when a CV or job
    # specification edit reaches the approval gate.
    frozen_spec_fp = evidence.get("evaluation_spec_fingerprint")
    current_spec_fp = _text_fingerprint(getattr(role, "job_spec_text", None))
    if frozen_spec_fp and current_spec_fp and str(frozen_spec_fp) != current_spec_fp:
        add_reason("criteria_changed")

    frozen_cv_fp = (
        evidence.get("evaluation_cv_fingerprint")
        or decision.cv_fingerprint
        or fingerprint.get("cv_fingerprint")
    )
    current_cv_fp = _text_fingerprint(_application_cv_text(application))
    if frozen_cv_fp and current_cv_fp and str(frozen_cv_fp) != current_cv_fp:
        add_reason("cv_replaced")

    cv_uploaded_at_at_emit = fingerprint.get("cv_uploaded_at")
    current_cv_uploaded_at = getattr(application, "cv_uploaded_at", None)
    if (
        current_cv_uploaded_at is not None
        and cv_uploaded_at_at_emit is not None
        and current_cv_uploaded_at.isoformat() != cv_uploaded_at_at_emit
    ):
        add_reason("cv_replaced")

    if fingerprint and role is not None:
        emitted_criteria_fp = decision.criteria_fingerprint or fingerprint.get(
            "criteria_fingerprint"
        )
        current_criteria_fp = criteria_content_fingerprint(
            db, int(role.id), cache=cache
        )
        if (emitted_criteria_fp or current_criteria_fp) and str(
            emitted_criteria_fp or ""
        ) != str(current_criteria_fp or ""):
            add_reason("criteria_changed")

        note_at_emit = _safe_int(fingerprint.get("last_recruiter_note_id"))
        current_note = _latest_recruiter_note_id(db, int(role.id), cache=cache)
        if current_note is not None and (
            note_at_emit is None or current_note > note_at_emit
        ):
            add_reason("recruiter_note_added")

    frozen_threshold = _first_score(evidence.get("effective_threshold"))
    current_threshold = None
    if role is not None:
        role_id = int(role.id)
        if cache is not None and role_id in cache.role_fit_threshold:
            current_threshold = cache.role_fit_threshold[role_id]
        else:
            current_threshold = resolve_role_fit_threshold(db, role=role)
            if cache is not None:
                cache.role_fit_threshold[role_id] = current_threshold
    if (
        frozen_threshold is not None
        and current_threshold is not None
        and float(frozen_threshold) != float(current_threshold)
    ):
        add_reason("threshold_changed")
        details["threshold_changed"] = {
            "at_emit": float(frozen_threshold),
            "current": float(current_threshold),
        }

    frozen_score = evidence.get("role_fit_score")
    current_score = evaluation.role_fit_score
    try:
        score_changed = (
            frozen_score is not None
            and current_score is not None
            and abs(float(frozen_score) - float(current_score)) >= SCORE_DRIFT_BAND
        )
    except (TypeError, ValueError):
        score_changed = False
    if score_changed:
        add_reason("role_fit_score_shifted")
        details["role_fit_score_shifted"] = {
            "at_emit": float(frozen_score),
            "current": float(current_score),
        }

    assessment_id = _safe_int(evidence.get("assessment_id"))
    if assessment_id is not None:
        if assessment is _ASSESSMENT_NOT_LOADED:
            assessment = (
                db.query(Assessment)
                .filter(
                    Assessment.id == assessment_id,
                    Assessment.organization_id == int(decision.organization_id),
                    Assessment.role_id == int(decision.role_id),
                    Assessment.application_id == int(decision.application_id),
                )
                .one_or_none()
            )
        valid_assessment = bool(
            isinstance(assessment, Assessment)
            and int(assessment.id) == assessment_id
            and int(assessment.role_id or 0) == int(decision.role_id)
            and int(assessment.application_id or 0) == int(decision.application_id)
            and not bool(assessment.is_voided)
            and _status(assessment.status) in _ASSESSMENT_TERMINAL_STATUSES
        )
        if not valid_assessment:
            add_reason("assessment_changed")
        else:
            frozen_assessment_score = _first_score(evidence.get("assessment_score"))
            current_assessment_score = _assessment_score(assessment)
            if (
                frozen_assessment_score is not None
                and current_assessment_score is not None
                and abs(frozen_assessment_score - current_assessment_score)
                >= SCORE_DRIFT_BAND
            ):
                add_reason("assessment_score_shifted")
                details["assessment_score_shifted"] = {
                    "at_emit": frozen_assessment_score,
                    "current": current_assessment_score,
                }
    elif assessment is _ASSESSMENT_NOT_LOADED:
        # A newly created role-owned assessment is a material new input even
        # when the discarded pre-assessment decision did not cite one.
        assessment = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id == int(decision.organization_id),
                Assessment.role_id == int(decision.role_id),
                Assessment.application_id == int(decision.application_id),
                Assessment.is_voided.is_(False),
            )
            .order_by(Assessment.created_at.desc(), Assessment.id.desc())
            .first()
        )
        if assessment is not None:
            add_reason("assessment_changed")

    evaluation_details = (
        evaluation.details if isinstance(evaluation.details, dict) else {}
    )
    if is_engine_outdated(evaluation_details):
        add_reason("engine_outdated")
        details["engine_outdated"] = {
            "engine_version": resolve_engine_version(evaluation_details) or None
        }

    if not reasons:
        return StalenessReport(is_stale=False)
    summary = (
        "This role's score was produced by an older scoring engine."
        if reasons == ["engine_outdated"]
        else "This role's evaluation changed after the decision was queued."
    )
    return StalenessReport(
        is_stale=True,
        reasons=reasons,
        summary=summary,
        details=details,
    )


__all__ = ["related_decision_staleness"]
