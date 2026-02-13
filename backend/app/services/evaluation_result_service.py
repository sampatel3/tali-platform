from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .evaluation_service import calculate_weighted_rubric_score


ALLOWED_MANUAL_SCORES = {"excellent", "good", "poor"}


def _to_evidence_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [line.strip() for line in value.splitlines()]
    elif value is None:
        items = []
    else:
        items = [str(value).strip()]
    return [item for item in items if item]


def _to_notes_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [line.strip() for line in value.splitlines()]
    else:
        items = []
    return [item for item in items if item]


def _safe_weight(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalized_category_scores(
    category_scores: Dict[str, Any],
    evaluation_rubric: Dict[str, Any],
    require_evidence_for_scored: bool = False,
) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {}
    for category, raw_value in (category_scores or {}).items():
        data = raw_value if isinstance(raw_value, dict) else {"score": raw_value}
        score = str((data.get("score") or "")).strip().lower()
        if score and score not in ALLOWED_MANUAL_SCORES:
            raise ValueError(f"Score for {category} must be one of excellent, good, poor")
        evidence = _to_evidence_list(data.get("evidence"))
        if score and require_evidence_for_scored and not evidence:
            raise ValueError(f"Evidence is required for scored category '{category}'")
        weight = _safe_weight((evaluation_rubric.get(category) or {}).get("weight"))
        normalized[category] = {
            "score": score or None,
            "weight": weight,
            "evidence": evidence,
        }
    return normalized


def _overall_score(
    category_scores: Dict[str, Dict[str, Any]],
    evaluation_rubric: Dict[str, Any],
) -> Optional[float]:
    flat_scores = {
        category: details.get("score")
        for category, details in category_scores.items()
        if isinstance(details, dict) and details.get("score")
    }
    if not flat_scores:
        return None
    # Converts weighted grade scale (1..3) into recruiter-visible 0..10 range.
    return round(calculate_weighted_rubric_score(flat_scores, evaluation_rubric) * (10.0 / 3.0), 2)


def build_evaluation_result(
    *,
    assessment_id: int,
    completed_due_to_timeout: bool,
    evaluation_rubric: Dict[str, Any],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    category_scores = _normalized_category_scores(
        body.get("category_scores") or {},
        evaluation_rubric,
        require_evidence_for_scored=True,
    )
    return {
        "assessment_id": assessment_id,
        "completed_due_to_timeout": bool(completed_due_to_timeout),
        "category_scores": category_scores,
        "overall_score": _overall_score(category_scores, evaluation_rubric),
        "strengths": _to_notes_list(body.get("strengths")),
        "improvements": _to_notes_list(body.get("improvements")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_stored_evaluation_result(
    raw: Dict[str, Any] | None,
    *,
    assessment_id: int,
    completed_due_to_timeout: bool,
    evaluation_rubric: Dict[str, Any],
) -> Dict[str, Any] | None:
    if not raw or not isinstance(raw, dict):
        return None
    category_scores = _normalized_category_scores(
        raw.get("category_scores") or {},
        evaluation_rubric,
        require_evidence_for_scored=False,
    )
    return {
        "assessment_id": raw.get("assessment_id") or assessment_id,
        "completed_due_to_timeout": bool(
            raw.get("completed_due_to_timeout", completed_due_to_timeout)
        ),
        "category_scores": category_scores,
        "overall_score": raw.get("overall_score")
        if raw.get("overall_score") is not None
        else _overall_score(category_scores, evaluation_rubric),
        "strengths": _to_notes_list(raw.get("strengths")),
        "improvements": _to_notes_list(raw.get("improvements")),
        "updated_at": raw.get("updated_at"),
    }
