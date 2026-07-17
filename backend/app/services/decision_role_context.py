"""Role-local presentation and freshness for decisions over a shared ATS row.

Candidate identity and the provider application are intentionally shared across
related roles. Scoring output is not. This module is the boundary used by the
Decision Hub: once ``decision.role_id`` differs from ``application.role_id``,
score-derived fields may come only from that role's ``SisterRoleEvaluation``
or from immutable evidence frozen on the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from types import SimpleNamespace
from typing import Iterable

from sqlalchemy.orm import Session

from ..cv_matching.holistic import resolve_engine_version
from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_RUNNING,
    SisterRoleEvaluation,
)
from .decision_role_staleness import (
    _first_score,
    _safe_int,
    related_decision_staleness,
)


_IN_FLIGHT_STATUSES = {
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_RETRY_WAIT,
}


def is_cross_role_decision(
    decision: AgentDecision,
    application: CandidateApplication | None,
) -> bool:
    return bool(
        application is not None
        and int(decision.role_id) != int(application.role_id)
    )


def effective_workable_job_id(role: Role | None) -> str | None:
    """Return the one ATS job backing a role's shared application pool."""

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
    """Batch-load the assessment frozen on each related-role decision.

    The map deliberately includes ``None`` entries for cross-role decisions
    without a valid assessment row. Callers can therefore distinguish "already
    batch-checked and missing" from "not loaded" and avoid an N+1 fallback.
    """

    decision_assessment_ids: dict[int, int | None] = {}
    organization_ids: set[int] = set()
    for decision in decisions:
        application = applications_by_id.get(int(decision.application_id))
        if not is_cross_role_decision(decision, application):
            continue
        evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
        decision_assessment_ids[int(decision.id)] = _safe_int(
            evidence.get("assessment_id")
        )
        organization_ids.add(int(decision.organization_id))

    assessment_ids = {
        assessment_id
        for assessment_id in decision_assessment_ids.values()
        if assessment_id is not None
    }
    assessments_by_id: dict[int, Assessment] = {}
    if assessment_ids:
        assessments_by_id = {
            int(assessment.id): assessment
            for assessment in db.query(Assessment)
            .filter(
                Assessment.id.in_(assessment_ids),
                Assessment.organization_id.in_(organization_ids),
            )
            .all()
        }
    return {
        decision_id: assessments_by_id.get(assessment_id)
        for decision_id, assessment_id in decision_assessment_ids.items()
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
