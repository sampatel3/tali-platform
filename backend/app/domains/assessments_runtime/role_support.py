from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...schemas.role import ApplicationResponse, RoleResponse
from ...services.taali_scoring import (
    ROLE_FIT_WEIGHTS,
    TAALI_SCORING_RUBRIC_VERSION,
    TAALI_WEIGHTS,
    compute_role_fit_score,
    compute_taali_score,
)
from .pipeline_service import (
    ensure_pipeline_fields,
    stage_external_drift,
)

_ROLE_CRITERIA_MAX_ITEMS = 12
_ROLE_CRITERION_MAX_CHARS = 500
_ROLE_METADATA_PREFIXES = (
    "location:",
    "department:",
    "employment type:",
    "apply:",
    "state:",
    "posted:",
)


def _normalize_cv_match_score_for_response(score: float | None, details: dict | None) -> float | None:
    if score is None:
        return None
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    scale = str((details or {}).get("score_scale") or "").strip().lower()
    if "100" in scale:
        normalized = numeric
    elif "10" in scale and "100" not in scale:
        normalized = numeric * 10.0
    elif numeric <= 10.0:
        normalized = numeric * 10.0
    else:
        normalized = numeric
    return round(max(0.0, min(100.0, normalized)), 1)


def role_has_job_spec(role: Role) -> bool:
    return bool(
        (role.job_spec_file_url or "").strip()
        or (role.job_spec_text or "").strip()
        or (role.description or "").strip()
    )


def get_role(role_id: int, org_id: int, db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def get_application(application_id: int, org_id: int, db: Session) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
            joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
        )
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _clean_criterion_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .;,-")
    return text[:_ROLE_CRITERION_MAX_CHARS]


def _criterion_id(text: str, source: str, *, order: int) -> str:
    digest = hashlib.sha1(f"{source}:{text.lower()}:{order}".encode("utf-8")).hexdigest()[:12]
    prefix = "jd" if source == "job_spec" else "rec"
    return f"{prefix}_{digest}"


def _split_requirement_text(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []

    parts = [raw]
    if "\n" in raw or ";" in raw:
        parts = re.split(r"[\n;]+", raw)
    elif len(raw) > 140:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", raw)

    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = _clean_criterion_text(part)
        lowered = cleaned.lower()
        if len(cleaned) < 12 or lowered in seen:
            continue
        if lowered.startswith("http") or "workable.com/" in lowered:
            continue
        seen.add(lowered)
        out.append(cleaned)
        if len(out) >= _ROLE_CRITERIA_MAX_ITEMS:
            break
    return out


def _derive_job_spec_criteria(role: Role, *, max_items: int = 4) -> list[str]:
    source_text = str(role.job_spec_text or role.description or "").strip()
    if not source_text:
        return []

    lines: list[str] = []
    for raw_line in re.split(r"[\r\n]+", source_text):
        cleaned = _clean_criterion_text(raw_line)
        lowered = cleaned.lower()
        if len(cleaned) < 18:
            continue
        if lowered.startswith(_ROLE_METADATA_PREFIXES):
            continue
        if "workable.com/" in lowered:
            continue
        lines.append(cleaned)

    if len(lines) < 2:
        paragraph_text = re.sub(r"\s+", " ", source_text)
        sentence_candidates = [
            _clean_criterion_text(part)
            for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph_text)
        ]
        lines.extend([part for part in sentence_candidates if len(part) >= 18])

    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(line)
        if len(out) >= max_items:
            break
    return out


def _criteria_from_texts(texts: list[str], *, source: str, start_order: int) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    next_order = start_order
    for text in texts:
        cleaned = _clean_criterion_text(text)
        if not cleaned:
            continue
        criteria.append(
            {
                "id": _criterion_id(cleaned, source, order=next_order),
                "text": cleaned,
                "source": source,
                "order": next_order,
                "weight": None,
            }
        )
        next_order += 1
    return criteria


def default_role_scoring_criteria(role: Role) -> list[dict[str, Any]]:
    recruiter_texts = _split_requirement_text(role.additional_requirements or "")
    job_spec_texts = _derive_job_spec_criteria(
        role,
        max_items=3 if recruiter_texts else 4,
    )
    criteria = _criteria_from_texts(job_spec_texts, source="job_spec", start_order=1)
    criteria.extend(
        _criteria_from_texts(recruiter_texts, source="recruiter", start_order=len(criteria) + 1)
    )
    return criteria[:_ROLE_CRITERIA_MAX_ITEMS]


def normalize_role_scoring_criteria(role: Role, raw_criteria: Any | None = None) -> list[dict[str, Any]]:
    source_items = raw_criteria if raw_criteria is not None else role.scoring_criteria
    if not isinstance(source_items, list) or not source_items:
        return default_role_scoring_criteria(role)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(source_items, start=1):
        if isinstance(item, str):
            text = _clean_criterion_text(item)
            source = "job_spec"
            weight = None
            order = index
            criterion_id = ""
        elif isinstance(item, dict):
            text = _clean_criterion_text(item.get("text") or item.get("criterion") or item.get("name"))
            source = str(item.get("source") or "job_spec").strip().lower()
            source = source if source in {"job_spec", "recruiter"} else "job_spec"
            weight = item.get("weight")
            try:
                weight = int(weight) if weight is not None else None
            except (TypeError, ValueError):
                weight = None
            try:
                order = int(item.get("order") or index)
            except (TypeError, ValueError):
                order = index
            criterion_id = str(item.get("id") or "").strip()
        else:
            continue

        if not text:
            continue
        key = f"{source}:{text.lower()}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "id": criterion_id or _criterion_id(text, source, order=order),
                "text": text,
                "source": source,
                "order": max(1, order),
                "weight": weight if weight is not None and weight > 0 else None,
            }
        )

    if not normalized:
        return default_role_scoring_criteria(role)

    normalized.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))
    for order, item in enumerate(normalized, start=1):
        item["order"] = order
        item["id"] = item.get("id") or _criterion_id(item["text"], item["source"], order=order)
    return normalized[:_ROLE_CRITERIA_MAX_ITEMS]


def set_role_scoring_criteria(role: Role, criteria: Any) -> list[dict[str, Any]]:
    normalized = normalize_role_scoring_criteria(role, criteria)
    role.scoring_criteria = normalized

    recruiter_criteria = [
        item["text"]
        for item in normalized
        if str(item.get("source") or "").strip().lower() == "recruiter"
    ]
    role.additional_requirements = "\n".join(recruiter_criteria) or None
    return normalized


def refresh_role_job_spec_criteria(role: Role) -> list[dict[str, Any]]:
    existing = normalize_role_scoring_criteria(role)
    recruiter = [item for item in existing if item.get("source") == "recruiter"]
    job_spec = _criteria_from_texts(
        _derive_job_spec_criteria(role, max_items=3 if recruiter else 4),
        source="job_spec",
        start_order=1,
    )
    merged = job_spec + [
        {
            **item,
            "order": len(job_spec) + index,
        }
        for index, item in enumerate(recruiter, start=1)
    ]
    return set_role_scoring_criteria(role, merged)


def role_scoring_requirements_text(role: Role) -> str | None:
    criteria = normalize_role_scoring_criteria(role)
    if not criteria:
        return (role.additional_requirements or "").strip() or None
    lines = [item["text"] for item in criteria if item.get("text")]
    return "\n".join(lines).strip() or None


def role_reject_threshold(role: Role | None) -> int:
    try:
        threshold = int(getattr(role, "reject_threshold", 60) or 60)
    except (TypeError, ValueError):
        threshold = 60
    return max(0, min(100, threshold))


def application_is_below_threshold(app: CandidateApplication) -> bool:
    score = _normalize_cv_match_score_for_response(
        getattr(app, "cv_match_score", None),
        app.cv_match_details if isinstance(getattr(app, "cv_match_details", None), dict) else None,
    )
    if score is None:
        return False
    return score < float(role_reject_threshold(getattr(app, "role", None)))


def role_to_response(
    role: Role,
    *,
    tasks_count: int | None = None,
    applications_count: int | None = None,
    stage_counts: dict[str, int] | None = None,
    active_candidates_count: int | None = None,
    last_candidate_activity_at: datetime | None = None,
) -> RoleResponse:
    if tasks_count is None:
        tasks_count = len(role.tasks or [])
    if applications_count is None:
        applications_count = len(
            [a for a in (role.applications or []) if getattr(a, "deleted_at", None) is None]
        )

    return RoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        description=role.description,
        additional_requirements=role.additional_requirements,
        source=role.source,
        workable_job_id=role.workable_job_id,
        job_spec_filename=role.job_spec_filename,
        job_spec_text=role.job_spec_text,
        job_spec_uploaded_at=role.job_spec_uploaded_at,
        job_spec_present=role_has_job_spec(role),
        scoring_criteria=normalize_role_scoring_criteria(role),
        reject_threshold=role_reject_threshold(role),
        interview_focus=role.interview_focus,
        interview_focus_generated_at=role.interview_focus_generated_at,
        tasks_count=tasks_count,
        applications_count=applications_count,
        stage_counts=stage_counts or {},
        active_candidates_count=int(active_candidates_count or 0),
        last_candidate_activity_at=last_candidate_activity_at,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _candidate_location(candidate) -> str | None:
    if not candidate:
        return None
    city = (candidate.location_city or "").strip()
    country = (candidate.location_country or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country or None


def _assessment_status_value(assessment: Assessment | None) -> str | None:
    if not assessment:
        return None
    status = getattr(assessment, "status", None)
    return status.value if hasattr(status, "value") else (str(status) if status is not None else None)


def _is_completed_assessment(assessment: Assessment | None) -> bool:
    status = _assessment_status_value(assessment)
    return status in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _sort_dt(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _requirements_fit_score(details: dict | None) -> float | None:
    if not isinstance(details, dict):
        return None
    raw = details.get("requirements_match_score_100")
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(100.0, numeric)), 1)


def _assessment_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    for value in (
        getattr(assessment, "assessment_score", None),
        getattr(assessment, "final_score", None),
    ):
        try:
            if value is not None:
                return round(max(0.0, min(100.0, float(value))), 1)
        except (TypeError, ValueError):
            continue

    score_10 = getattr(assessment, "score", None)
    try:
        if score_10 is not None:
            return round(max(0.0, min(100.0, float(score_10) * 10.0)), 1)
    except (TypeError, ValueError):
        return None
    return None


def _assessment_taali_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    try:
        if getattr(assessment, "taali_score", None) is not None:
            return round(max(0.0, min(100.0, float(assessment.taali_score))), 1)
    except (TypeError, ValueError):
        return None

    assessment_score = _assessment_score_100(assessment)
    role_fit_score = _assessment_role_fit_score_100(assessment)
    taali_score = compute_taali_score(assessment_score, role_fit_score)
    if taali_score is not None:
        return taali_score

    if assessment_score is None:
        return role_fit_score
    if role_fit_score is None:
        return assessment_score
    return taali_score


def _assessment_role_fit_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    score_breakdown = (
        assessment.score_breakdown
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    score_components = score_breakdown.get("score_components") if isinstance(score_breakdown, dict) else {}
    if isinstance(score_components, dict):
        try:
            if score_components.get("role_fit_score") is not None:
                return round(max(0.0, min(100.0, float(score_components.get("role_fit_score")))), 1)
        except (TypeError, ValueError):
            pass

    raw_details = (
        assessment.cv_job_match_details
        if isinstance(getattr(assessment, "cv_job_match_details", None), dict)
        else None
    )
    cv_fit_score = _normalize_cv_match_score_for_response(
        getattr(assessment, "cv_job_match_score", None),
        raw_details,
    )
    requirements_fit_score = _requirements_fit_score(raw_details)
    return compute_role_fit_score(cv_fit_score, requirements_fit_score)


def _dimension_extremes(category_scores: dict[str, Any] | None) -> tuple[str | None, str | None]:
    numeric_scores = []
    for key, value in (category_scores or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        numeric_scores.append((key, numeric))
    if not numeric_scores:
        return None, None
    strongest = max(numeric_scores, key=lambda item: item[1])[0]
    weakest = min(numeric_scores, key=lambda item: item[1])[0]
    return strongest, weakest


def _active_assessments_for_application(app: CandidateApplication) -> list[Assessment]:
    assessments = [
        assessment
        for assessment in (app.assessments or [])
        if not bool(getattr(assessment, "is_voided", False))
    ]
    return sorted(
        assessments,
        key=lambda assessment: (
            _sort_dt(getattr(assessment, "completed_at", None)),
            _sort_dt(getattr(assessment, "created_at", None)),
            int(getattr(assessment, "id", 0) or 0),
        ),
        reverse=True,
    )


def latest_valid_role_assessment(
    *,
    candidate_id: int | None,
    role_id: int | None,
    org_id: int,
    db: Session,
) -> Assessment | None:
    if not candidate_id or not role_id:
        return None
    return (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.candidate_id == candidate_id,
            Assessment.role_id == role_id,
            Assessment.is_voided.is_(False),
        )
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )


def completed_valid_role_assessment(
    *,
    candidate_id: int | None,
    role_id: int | None,
    org_id: int,
    db: Session,
) -> Assessment | None:
    if not candidate_id or not role_id:
        return None
    return (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.candidate_id == candidate_id,
            Assessment.role_id == role_id,
            Assessment.is_voided.is_(False),
            Assessment.status.in_(
                [
                    AssessmentStatus.COMPLETED,
                    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                ]
            ),
        )
        .order_by(Assessment.completed_at.desc(), Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )


def _score_summary_for_application(app: CandidateApplication) -> dict[str, Any]:
    active_assessments = _active_assessments_for_application(app)
    latest_assessment = active_assessments[0] if active_assessments else None
    completed_assessment = next((assessment for assessment in active_assessments if _is_completed_assessment(assessment)), None)

    app_cv_details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    app_cv_fit = _normalize_cv_match_score_for_response(app.cv_match_score, app_cv_details)
    app_requirements_fit = _requirements_fit_score(app_cv_details)
    app_role_fit = compute_role_fit_score(app_cv_fit, app_requirements_fit)

    if completed_assessment:
        assessment_details = (
            completed_assessment.cv_job_match_details
            if isinstance(getattr(completed_assessment, "cv_job_match_details", None), dict)
            else {}
        )
        cv_fit_score = _normalize_cv_match_score_for_response(
            getattr(completed_assessment, "cv_job_match_score", None),
            assessment_details,
        )
        requirements_fit_score = _requirements_fit_score(assessment_details)
        role_fit_score = _assessment_role_fit_score_100(completed_assessment)
        assessment_score = _assessment_score_100(completed_assessment)
        taali_score = _assessment_taali_score_100(completed_assessment)
        mode = "assessment_plus_role_fit" if role_fit_score is not None else "assessment_only_fallback"
        assessment_status = _assessment_status_value(completed_assessment)
        assessment_id = completed_assessment.id
        assessment_completed_at = completed_assessment.completed_at
    else:
        cv_fit_score = app_cv_fit
        requirements_fit_score = app_requirements_fit
        role_fit_score = app_role_fit
        assessment_score = None
        taali_score = app_role_fit
        assessment_status = _assessment_status_value(latest_assessment)
        assessment_id = latest_assessment.id if latest_assessment else None
        assessment_completed_at = None
        mode = "role_fit_only" if app_role_fit is not None else "pending"

    return {
        "taali_score": taali_score,
        "assessment_score": assessment_score,
        "role_fit_score": role_fit_score,
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
        "role_fit_components": {
            "cv_fit_score": cv_fit_score,
            "requirements_fit_score": requirements_fit_score,
        },
        "weights": {
            "cv_fit_score": ROLE_FIT_WEIGHTS["cv_fit"],
            "requirements_fit_score": ROLE_FIT_WEIGHTS["requirements_fit"],
            "assessment_score": TAALI_WEIGHTS["assessment"],
            "role_fit_score": TAALI_WEIGHTS["role_fit"],
        },
        "mode": mode,
        "formula_label": (
            "TAALI Score = 50% Assessment + 50% Role fit"
            if mode == "assessment_plus_role_fit"
            else (
                "TAALI Score currently reflects Assessment only"
                if mode == "assessment_only_fallback"
                else "TAALI Score currently reflects Role fit until assessment signal is available"
            )
        ),
        "score_rubric_version": TAALI_SCORING_RUBRIC_VERSION,
        "assessment_id": assessment_id,
        "assessment_status": assessment_status,
        "assessment_completed_at": assessment_completed_at,
        "has_voided_attempts": any(bool(getattr(assessment, "is_voided", False)) for assessment in (app.assessments or [])),
    }


def _assessment_preview_for_application(app: CandidateApplication) -> dict[str, Any] | None:
    completed_assessment = next(
        (assessment for assessment in _active_assessments_for_application(app) if _is_completed_assessment(assessment)),
        None,
    )
    if not completed_assessment:
        return None

    score_breakdown = (
        completed_assessment.score_breakdown
        if isinstance(getattr(completed_assessment, "score_breakdown", None), dict)
        else {}
    )
    category_scores = score_breakdown.get("category_scores") or (
        completed_assessment.prompt_analytics.get("category_scores")
        if isinstance(getattr(completed_assessment, "prompt_analytics", None), dict)
        else {}
    )
    strongest_dimension, weakest_dimension = _dimension_extremes(category_scores if isinstance(category_scores, dict) else {})

    return {
        "assessment_id": completed_assessment.id,
        "task_name": completed_assessment.task.name if getattr(completed_assessment, "task", None) else None,
        "taali_score": _assessment_taali_score_100(completed_assessment),
        "assessment_score": _assessment_score_100(completed_assessment),
        "role_fit_score": _assessment_role_fit_score_100(completed_assessment),
        "category_scores": category_scores if isinstance(category_scores, dict) else {},
        "heuristic_summary": score_breakdown.get("heuristic_summary"),
        "strongest_dimension": strongest_dimension,
        "weakest_dimension": weakest_dimension,
        "completed_at": completed_assessment.completed_at,
        "status": _assessment_status_value(completed_assessment),
        "is_voided": bool(getattr(completed_assessment, "is_voided", False)),
    }


def _assessment_history_for_application(app: CandidateApplication) -> list[dict[str, Any]]:
    history = sorted(
        list(app.assessments or []),
        key=lambda assessment: (
            _sort_dt(getattr(assessment, "completed_at", None)),
            _sort_dt(getattr(assessment, "created_at", None)),
            int(getattr(assessment, "id", 0) or 0),
        ),
        reverse=True,
    )
    return [
        {
            "assessment_id": assessment.id,
            "task_name": assessment.task.name if getattr(assessment, "task", None) else None,
            "status": _assessment_status_value(assessment),
            "assessment_score": _assessment_score_100(assessment),
            "taali_score": _assessment_taali_score_100(assessment),
            "role_fit_score": _assessment_role_fit_score_100(assessment),
            "created_at": assessment.created_at,
            "completed_at": assessment.completed_at,
            "is_voided": bool(getattr(assessment, "is_voided", False)),
            "voided_at": getattr(assessment, "voided_at", None),
            "void_reason": getattr(assessment, "void_reason", None),
            "superseded_by_assessment_id": getattr(assessment, "superseded_by_assessment_id", None),
        }
        for assessment in history
    ]


def application_to_response(app: CandidateApplication) -> ApplicationResponse:
    ensure_pipeline_fields(app)
    candidate = app.candidate
    raw_details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    cv_match_score = _normalize_cv_match_score_for_response(app.cv_match_score, raw_details)
    cv_match_details = dict(raw_details)
    if cv_match_score is not None and "score_scale" not in cv_match_details:
        cv_match_details["score_scale"] = "0-100"
    score_summary = _score_summary_for_application(app)
    role_threshold = role_reject_threshold(app.role)

    return ApplicationResponse(
        id=app.id,
        organization_id=app.organization_id,
        candidate_id=app.candidate_id,
        role_id=app.role_id,
        status=app.status,
        pipeline_stage=app.pipeline_stage,
        pipeline_stage_updated_at=app.pipeline_stage_updated_at,
        pipeline_stage_source=app.pipeline_stage_source,
        application_outcome=app.application_outcome,
        application_outcome_updated_at=app.application_outcome_updated_at,
        external_refs=(app.external_refs if isinstance(app.external_refs, dict) else None),
        external_stage_raw=app.external_stage_raw,
        external_stage_normalized=app.external_stage_normalized,
        integration_sync_state=(app.integration_sync_state if isinstance(app.integration_sync_state, dict) else None),
        pipeline_external_drift=stage_external_drift(app),
        version=int(app.version or 1),
        notes=app.notes,
        candidate_email=(candidate.email if candidate else ""),
        candidate_name=(candidate.full_name if candidate else None),
        candidate_position=(candidate.position if candidate else None),
        cv_filename=app.cv_filename,
        cv_uploaded_at=app.cv_uploaded_at,
        cv_match_score=cv_match_score,
        cv_match_details=cv_match_details or None,
        cv_match_scored_at=app.cv_match_scored_at,
        source=app.source,
        workable_candidate_id=app.workable_candidate_id,
        workable_stage=app.workable_stage,
        workable_score_raw=app.workable_score_raw,
        workable_score=app.workable_score,
        workable_score_source=app.workable_score_source,
        rank_score=app.rank_score,
        candidate_headline=(candidate.headline if candidate else None),
        candidate_image_url=(candidate.image_url if candidate else None),
        candidate_location=_candidate_location(candidate),
        candidate_phone=(candidate.phone if candidate else None),
        candidate_profile_url=(candidate.profile_url if candidate else None),
        candidate_social_profiles=(candidate.social_profiles if candidate else None),
        candidate_tags=(candidate.tags if candidate else None),
        candidate_skills=(candidate.skills if candidate else None),
        candidate_education=(candidate.education_entries if candidate else None),
        candidate_experience=(candidate.experience_entries if candidate else None),
        candidate_summary=(candidate.summary if candidate else None),
        candidate_workable_created_at=(candidate.workable_created_at if candidate else None),
        workable_sourced=app.workable_sourced,
        workable_profile_url=app.workable_profile_url,
        workable_enriched=(candidate.workable_enriched if candidate else None),
        taali_score=score_summary.get("taali_score"),
        score_mode=score_summary.get("mode"),
        valid_assessment_id=score_summary.get("assessment_id"),
        valid_assessment_status=score_summary.get("assessment_status"),
        score_summary=score_summary,
        role_reject_threshold=role_threshold,
        below_role_threshold=(
            cv_match_score is not None
            and cv_match_score < float(role_threshold)
        ),
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def application_detail_payload(app: CandidateApplication, *, include_cv_text: bool) -> dict[str, Any]:
    data = application_to_response(app)
    payload = data.model_dump()
    if include_cv_text:
        cv = (app.cv_text or "").strip()
        if not cv and app.candidate:
            cv = (app.candidate.cv_text or "").strip()
        payload["cv_text"] = cv or None
    else:
        payload["cv_text"] = None
    payload["assessment_preview"] = _assessment_preview_for_application(app)
    payload["assessment_history"] = _assessment_history_for_application(app)
    return payload
