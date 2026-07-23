"""Role-local presentation and freshness for related-role decisions.

Related-role membership is explicit and may use either an owner-role evidence
row or a direct application. Score and funnel state always come from that
role's ``SisterRoleEvaluation``; an optional ATS application is transport and
restriction context only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timezone
from types import SimpleNamespace
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..candidate_search.assessment_score_truth import (
    assessment_taali_score_100,
    latest_completed_role_assessment,
    latest_role_assessment,
    role_assessment_truth,
)
from ..cv_matching.holistic import is_engine_outdated, resolve_engine_version
from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_STALE_HELD,
    SisterRoleEvaluation,
)
from .auto_threshold_service import resolve_role_fit_threshold
from .decision_staleness import (
    SCORE_DRIFT_BAND,
    StalenessCache,
    StalenessReport,
    _latest_recruiter_note_id,
    criteria_content_fingerprint,
)
from .decision_policy_generation import policy_generation_drift
from .related_role_application_runtime import role_application_is_resolved


_IN_FLIGHT_STATUSES = {
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_RETRY_WAIT,
}
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


def is_cross_role_decision(
    decision: AgentDecision,
    application: CandidateApplication | None,
) -> bool:
    if application is None:
        return False
    if int(decision.role_id) != int(application.role_id):
        return True
    role = getattr(decision, "role", None) or getattr(application, "role", None)
    if role is not None:
        from .logical_role_batch_operations import is_related_role

        if is_related_role(role):
            return True
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    return bool(
        evidence.get("related_role_id")
        or evidence.get("related_role_membership_id")
        or evidence.get("shared_ats_application")
    )


def effective_workable_job_id(role: Role | None) -> str | None:
    """Return the ATS job available as this role's optional write transport."""

    if role is None:
        return None
    operational_role = role
    if str(getattr(role, "role_kind", None) or "") == "sister":
        owner = getattr(role, "ats_owner_role", None)
        if (
            owner is not None
            and getattr(owner, "organization_id", None) == role.organization_id
            and getattr(owner, "deleted_at", None) is None
        ):
            operational_role = owner
    return getattr(operational_role, "workable_job_id", None)


def load_related_evaluation(
    db: Session,
    *,
    decision: AgentDecision,
    application: CandidateApplication | None,
) -> SisterRoleEvaluation | None:
    if not is_cross_role_decision(decision, application):
        return None
    return (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(decision.organization_id),
            SisterRoleEvaluation.role_id == int(decision.role_id),
            SisterRoleEvaluation.source_application_id
            == int(decision.application_id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .one_or_none()
    )


def load_related_evaluation_map(
    db: Session,
    *,
    decisions: Iterable[AgentDecision],
    applications_by_id: dict[int, CandidateApplication],
) -> dict[tuple[int, int], SisterRoleEvaluation]:
    """Batch-load exact role/application evaluation pairs for a Hub page."""

    decisions = list(decisions)
    keys = {
        (int(decision.role_id), int(decision.application_id))
        for decision in decisions
        if is_cross_role_decision(
            decision,
            applications_by_id.get(int(decision.application_id)),
        )
    }
    if not keys:
        return {}
    role_ids = {role_id for role_id, _ in keys}
    application_ids = {application_id for _, application_id in keys}
    organization_ids = {int(decision.organization_id) for decision in decisions}
    rows = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id.in_(organization_ids),
            SisterRoleEvaluation.role_id.in_(role_ids),
            SisterRoleEvaluation.source_application_id.in_(application_ids),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .all()
    )
    return {
        (int(row.role_id), int(row.source_application_id)): row
        for row in rows
        if (int(row.role_id), int(row.source_application_id)) in keys
    }


def load_related_assessment_map(
    db: Session,
    *,
    decisions: Iterable[AgentDecision],
    applications_by_id: dict[int, CandidateApplication],
) -> dict[int, Assessment | None]:
    """Batch-load current assessment truth for each related-role decision.

    Assessment identity is ``(organization, logical role, candidate)``.
    ``application_id`` is transport metadata and may legitimately point at an
    ATS row different from the role membership's source evidence row.
    """

    decision_identities: dict[int, tuple[int, int, int, bool]] = {}
    for decision in decisions:
        application = applications_by_id.get(int(decision.application_id))
        if not is_cross_role_decision(decision, application):
            continue
        evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
        if _safe_int(evidence.get("assessment_id")) is None or application is None:
            decision_identities[int(decision.id)] = (
                int(decision.organization_id),
                int(decision.role_id),
                int(getattr(decision, "candidate_id", 0) or 0),
                True,
            )
            continue
        decision_identities[int(decision.id)] = (
            int(decision.organization_id),
            int(decision.role_id),
            int(application.candidate_id),
            str(decision.decision_type) != "resend_assessment_invite",
        )
    valid_identities = {
        (organization_id, role_id, candidate_id)
        for organization_id, role_id, candidate_id, _ in decision_identities.values()
        if candidate_id > 0
    }
    latest_any: dict[tuple[int, int, int], Assessment] = {}
    latest_completed: dict[tuple[int, int, int], Assessment] = {}
    if valid_identities:
        organization_ids = {item[0] for item in valid_identities}
        role_ids = {item[1] for item in valid_identities}
        candidate_ids = {item[2] for item in valid_identities}
        completed_rows = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id.in_(organization_ids),
                Assessment.role_id.in_(role_ids),
                Assessment.candidate_id.in_(candidate_ids),
                Assessment.is_voided.is_(False),
                Assessment.status.in_(
                    (
                        AssessmentStatus.COMPLETED,
                        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                    )
                ),
            )
            .order_by(
                Assessment.organization_id.asc(),
                Assessment.role_id.asc(),
                Assessment.candidate_id.asc(),
                Assessment.completed_at.desc().nullslast(),
                Assessment.created_at.desc().nullslast(),
                Assessment.id.desc(),
            )
            .all()
        )
        for row in completed_rows:
            key = (
                int(row.organization_id),
                int(row.role_id),
                int(row.candidate_id),
            )
            if key not in valid_identities:
                continue
            latest_completed.setdefault(key, row)
        if any(not item[3] for item in decision_identities.values()):
            any_rows = (
                db.query(Assessment)
                .filter(
                    Assessment.organization_id.in_(organization_ids),
                    Assessment.role_id.in_(role_ids),
                    Assessment.candidate_id.in_(candidate_ids),
                    Assessment.is_voided.is_(False),
                )
                .order_by(
                    Assessment.organization_id.asc(),
                    Assessment.role_id.asc(),
                    Assessment.candidate_id.asc(),
                    Assessment.created_at.desc().nullslast(),
                    Assessment.id.desc(),
                )
                .all()
            )
            for row in any_rows:
                key = (
                    int(row.organization_id),
                    int(row.role_id),
                    int(row.candidate_id),
                )
                if key in valid_identities:
                    latest_any.setdefault(key, row)
    return {
        decision_id: (
            latest_completed.get((organization_id, role_id, candidate_id))
            if completed_only
            else latest_any.get((organization_id, role_id, candidate_id))
        )
        for decision_id, (
            organization_id,
            role_id,
            candidate_id,
            completed_only,
        ) in decision_identities.items()
    }


def compact_requirements_from_details(
    details: object,
) -> list[dict[str, object]] | None:
    details = details if isinstance(details, dict) else {}
    items = details.get("requirements_assessment")
    if not isinstance(items, list) or not items:
        return None
    rows: list[dict[str, object]] = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        label = str(
            item.get("criterion_text") or item.get("requirement") or ""
        ).strip()
        if not label:
            continue
        raw_score = item.get("match_score")
        rows.append(
            {
                "label": label,
                "score": (
                    round(float(raw_score))
                    if isinstance(raw_score, (int, float))
                    else None
                ),
                "status": (
                    str(item.get("status") or "").strip().lower() or None
                ),
            }
        )
    return rows or None


def score_provenance_from_evaluation(
    evaluation: SisterRoleEvaluation,
) -> dict[str, object]:
    details = evaluation.details if isinstance(evaluation.details, dict) else {}
    try:
        engine_version = resolve_engine_version(details) or None
    except Exception:
        engine_version = None
    scored_at = evaluation.scored_at
    if scored_at is not None and scored_at.tzinfo is None:
        scored_at = scored_at.replace(tzinfo=timezone.utc)
    return {
        "source": "sister_role_evaluation",
        "label": "Related role fit",
        "engine_version": engine_version,
        "scored_at": scored_at.isoformat() if scored_at else None,
        "model": evaluation.model_version or None,
    }


def integrity_from_evaluation(
    evaluation: SisterRoleEvaluation,
    *,
    application: CandidateApplication,
) -> dict | None:
    """Build the canonical integrity readout from this role's score details."""

    from ..domains.assessments_runtime.role_support import _integrity_summary

    proxy = SimpleNamespace(
        cv_match_details=(
            evaluation.details if isinstance(evaluation.details, dict) else {}
        ),
        # Parsed CV sections are candidate input, not an owner-role score.
        cv_sections=getattr(application, "cv_sections", None),
    )
    try:
        return _integrity_summary(proxy)
    except Exception:
        # Presentation metadata must never prevent a decision from being queued
        # or make the recruiter's review feed unavailable.
        return None


def evaluation_rescore_in_flight(
    evaluation: SisterRoleEvaluation | None,
) -> bool:
    return bool(
        evaluation is not None and str(evaluation.status) in _IN_FLIGHT_STATUSES
    )


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


@dataclass(frozen=True)
class DecisionPresentationSources:
    scoring_application: CandidateApplication | None
    taali_score: float | None
    score_provenance: dict | None
    integrity: dict | None
    requirements: list[dict[str, object]] | None
    role_summary: object | None
    rescore_in_flight: bool


def resolve_decision_presentation(
    decision: AgentDecision,
    *,
    application: CandidateApplication | None,
    related_evaluation: SisterRoleEvaluation | None,
    rescore_in_flight: bool,
) -> DecisionPresentationSources:
    """Resolve every score-derived Hub field through one role boundary."""

    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    cross_role = is_cross_role_decision(decision, application)
    scoring_application = None if cross_role else application
    taali_score = None
    if str(decision.decision_type) != "skip_assessment_reject":
        taali_score = _first_score(
            evidence.get("taali_score"),
            evidence.get("assessment_score"),
            evidence.get("role_fit_score"),
            (
                related_evaluation.role_fit_score
                if cross_role and related_evaluation is not None
                else None
            ),
            (
                scoring_application.taali_score_cache_100
                if scoring_application is not None
                else None
            ),
            (
                scoring_application.role_fit_score_cache_100
                if scoring_application is not None
                else None
            ),
        )

    frozen_provenance = evidence.get("score_provenance")
    provenance = (
        dict(frozen_provenance) if isinstance(frozen_provenance, dict) else None
    )
    frozen_integrity = evidence.get("integrity")
    integrity = dict(frozen_integrity) if isinstance(frozen_integrity, dict) else None
    if cross_role and related_evaluation is not None and application is not None:
        provenance = provenance or score_provenance_from_evaluation(
            related_evaluation
        )
        integrity = integrity or integrity_from_evaluation(
            related_evaluation, application=application
        )
    elif scoring_application is not None:
        try:
            from ..domains.assessments_runtime.role_support import (
                _integrity_summary,
                _score_provenance,
            )

            provenance = provenance or _score_provenance(scoring_application)
            integrity = integrity or _integrity_summary(scoring_application)
        except Exception:
            pass

    requirements = None
    if str(decision.decision_type) != "skip_assessment_reject":
        frozen_requirements = evidence.get("requirements")
        if isinstance(frozen_requirements, list):
            requirements = [
                dict(item) for item in frozen_requirements if isinstance(item, dict)
            ] or None
        elif cross_role:
            requirements = compact_requirements_from_details(
                getattr(related_evaluation, "details", None)
            )
        elif scoring_application is not None:
            requirements = compact_requirements_from_details(
                scoring_application.cv_match_details
            )

    return DecisionPresentationSources(
        scoring_application=scoring_application,
        taali_score=taali_score,
        score_provenance=provenance,
        integrity=integrity,
        requirements=requirements,
        role_summary=(
            getattr(related_evaluation, "summary", None) if cross_role else None
        ),
        rescore_in_flight=(
            evaluation_rescore_in_flight(related_evaluation)
            if cross_role
            else rescore_in_flight
        ),
    )


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
    if application is not None and role_application_is_resolved(
        db,
        role_id=int(decision.role_id),
        application=application,
    ):
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
    if status == SISTER_EVAL_STALE_HELD:
        return StalenessReport(
            is_stale=True,
            reasons=["related_role_inputs_changed"],
            summary=(
                "Candidate inputs changed after this decision. Select "
                "Re-evaluate to authorise a fresh role score."
            ),
            details={"evaluation_status": status},
        )
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

    if role is not None:
        policy_drift = policy_generation_drift(
            db,
            decision,
            role,
            cache.policy_generation if cache is not None else None,
        )
        if policy_drift is not None:
            add_reason("policy_generation_changed")
            details["policy_generation_changed"] = policy_drift

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
        emitted_criteria_fp = (
            decision.criteria_fingerprint
            or fingerprint.get("criteria_fingerprint")
        )
        current_criteria_fp = criteria_content_fingerprint(
            db, int(role.id), cache=cache
        )
        if (
            emitted_criteria_fp
            and current_criteria_fp
            and str(emitted_criteria_fp) != str(current_criteria_fp)
        ):
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
        candidate_id = _safe_int(
            getattr(application, "candidate_id", None)
            or getattr(decision, "candidate_id", None)
        )
        completed_assessment_expected = (
            str(decision.decision_type) != "resend_assessment_invite"
        )
        if assessment is _ASSESSMENT_NOT_LOADED:
            if candidate_id is None:
                assessment = None
            elif completed_assessment_expected:
                assessment = latest_completed_role_assessment(
                    db,
                    organization_id=int(decision.organization_id),
                    role_id=int(decision.role_id),
                    candidate_id=candidate_id,
                )
            else:
                assessment = latest_role_assessment(
                    db,
                    organization_id=int(decision.organization_id),
                    role_id=int(decision.role_id),
                    candidate_id=candidate_id,
                )
        valid_assessment = bool(
            isinstance(assessment, Assessment)
            and int(assessment.id) == assessment_id
            and int(assessment.organization_id or 0)
            == int(decision.organization_id)
            and int(assessment.role_id or 0) == int(decision.role_id)
            and candidate_id is not None
            and int(assessment.candidate_id or 0) == candidate_id
            and not bool(assessment.is_voided)
            and (
                not completed_assessment_expected
                or _status(assessment.status) in _ASSESSMENT_TERMINAL_STATUSES
            )
        )
        if not valid_assessment:
            add_reason("assessment_changed")
        else:
            truth = (
                role_assessment_truth(assessment)
                if completed_assessment_expected
                else None
            )
            if truth is not None and truth.grading_pending:
                add_reason("assessment_grading_incomplete")
                details["assessment_grading_incomplete"] = {
                    "state": truth.grading_state,
                    "scoring_partial": truth.scoring_partial,
                    "scoring_failed": truth.scoring_failed,
                }
            frozen_assessment_score = _first_score(
                evidence.get("assessment_taali_score"),
                evidence.get("taali_score"),
                # Compatibility with decisions emitted before TAALI and
                # technical-assessment evidence were stored separately.
                evidence.get("assessment_score"),
                fingerprint.get("taali_score_at_emit"),
            )
            current_assessment_score = (
                assessment_taali_score_100(assessment)
                if completed_assessment_expected
                else None
            )
            score_became_unavailable = (
                frozen_assessment_score is not None
                and current_assessment_score is None
                and completed_assessment_expected
            )
            score_drifted = (
                frozen_assessment_score is not None
                and current_assessment_score is not None
                and abs(frozen_assessment_score - current_assessment_score)
                >= SCORE_DRIFT_BAND
            )
            if score_became_unavailable or score_drifted:
                add_reason("assessment_score_shifted")
                details["assessment_score_shifted"] = {
                    "at_emit": frozen_assessment_score,
                    "current": current_assessment_score,
                }

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


__all__ = [
    "compact_requirements_from_details",
    "effective_workable_job_id",
    "evaluation_rescore_in_flight",
    "integrity_from_evaluation",
    "is_cross_role_decision",
    "load_related_assessment_map",
    "load_related_evaluation",
    "load_related_evaluation_map",
    "related_decision_staleness",
    "resolve_decision_presentation",
    "score_provenance_from_evaluation",
]
