"""Analytics endpoints for assessment metrics and benchmarking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...platform.database import get_db
from ...deps import get_current_user
from ...models.user import User
from ...models.assessment import Assessment, AssessmentStatus

router = APIRouter(prefix="/analytics", tags=["Analytics"])

_BENCHMARK_CACHE_TTL_SECONDS = 3600
_benchmark_cache: dict[Tuple[int, int], dict] = {}

_DIMENSION_ALIASES = {
    "task_completion": "task_completion",
    "prompt_clarity": "prompt_clarity",
    "context_provision": "context_provision",
    "independence_efficiency": "independence_efficiency",
    "response_utilization": "response_utilization",
    "debugging_design": "debugging_design",
    "written_communication": "written_communication",
    "role_fit": "role_fit",
    # Legacy aliases seen in older score breakdown payloads.
    "independence": "independence_efficiency",
    "utilization": "response_utilization",
    "communication": "written_communication",
    "approach": "debugging_design",
    "cv_match": "role_fit",
}

_DIMENSION_KEYS = [
    "task_completion",
    "prompt_clarity",
    "context_provision",
    "independence_efficiency",
    "response_utilization",
    "debugging_design",
    "written_communication",
    "role_fit",
]


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _status_value(assessment: Assessment) -> str:
    return str(getattr(assessment.status, "value", assessment.status) or "").lower()


def _is_completed(assessment: Assessment) -> bool:
    return _status_value(assessment) in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _parse_filter_datetime(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed = _ensure_utc(parsed)
    except Exception:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                parsed = parsed + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {value}") from exc
    if parsed and end_of_day and "T" not in raw and " " not in raw:
        parsed = parsed + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999)
    return parsed


def _score_100(assessment: Assessment) -> Optional[float]:
    final_score = getattr(assessment, "final_score", None)
    if isinstance(final_score, (int, float)):
        return float(final_score)
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score) * 10.0
    return None


def _score_10(assessment: Assessment) -> Optional[float]:
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    score100 = _score_100(assessment)
    if score100 is None:
        return None
    return score100 / 10.0


def _extract_category_scores(assessment: Assessment) -> Dict[str, float]:
    bucket = {}
    breakdown = assessment.score_breakdown if isinstance(assessment.score_breakdown, dict) else {}
    analytics = assessment.prompt_analytics if isinstance(assessment.prompt_analytics, dict) else {}

    raw_scores = (
        (breakdown.get("category_scores") if isinstance(breakdown.get("category_scores"), dict) else None)
        or (analytics.get("category_scores") if isinstance(analytics.get("category_scores"), dict) else None)
        or (
            analytics.get("detailed_scores", {}).get("category_scores")
            if isinstance(analytics.get("detailed_scores"), dict)
            and isinstance(analytics.get("detailed_scores", {}).get("category_scores"), dict)
            else None
        )
        or {}
    )
    for key, raw_value in raw_scores.items():
        canonical = _DIMENSION_ALIASES.get(str(key))
        if not canonical:
            continue
        if not isinstance(raw_value, (int, float)):
            continue
        bucket[canonical] = float(raw_value)
    return bucket


def _build_dimension_averages(assessments: Sequence[Assessment]) -> Dict[str, Optional[float]]:
    sums: dict[str, float] = {key: 0.0 for key in _DIMENSION_KEYS}
    counts: dict[str, int] = {key: 0 for key in _DIMENSION_KEYS}
    for assessment in assessments:
        scores = _extract_category_scores(assessment)
        for key, value in scores.items():
            if key not in sums:
                continue
            sums[key] += float(value)
            counts[key] += 1
    out: dict[str, Optional[float]] = {}
    for key in _DIMENSION_KEYS:
        out[key] = round(sums[key] / counts[key], 2) if counts[key] else None
    return out


def _build_score_buckets(scores_100: Sequence[float]) -> list[dict]:
    ranges = [
        ("0-20", 0, 20),
        ("20-40", 20, 40),
        ("40-60", 40, 60),
        ("60-80", 60, 80),
        ("80-100", 80, 101),
    ]
    total = len(scores_100)
    out = []
    for label, start, end in ranges:
        count = sum(1 for score in scores_100 if start <= score < end)
        percentage = round((count / total) * 100, 1) if total else 0.0
        out.append({"range": label, "count": count, "percentage": percentage})
    return out


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def _percentile_rank(values: Sequence[float], target: float) -> float:
    if not values:
        return 0.0
    count = sum(1 for value in values if value <= target)
    return round((count / len(values)) * 100.0, 1)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@router.get("/")
def get_analytics(
    role_id: Optional[int] = Query(default=None),
    task_id: Optional[int] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return filtered analytics for the current organization."""
    org_id = current_user.organization_id
    if not org_id:
        return {
            "weekly_completion": [],
            "total_assessments": 0,
            "total_candidates": 0,
            "total_tasks": 0,
            "completed_count": 0,
            "completion_rate": 0,
            "top_score": None,
            "avg_score": None,
            "avg_time_minutes": None,
            "avg_calibration_score": None,
            "score_buckets": _build_score_buckets([]),
            "dimension_averages": _build_dimension_averages([]),
        }

    parsed_from = _parse_filter_datetime(date_from, end_of_day=False)
    parsed_to = _parse_filter_datetime(date_to, end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    query = db.query(Assessment).filter(Assessment.organization_id == org_id)
    if role_id is not None:
        query = query.filter(Assessment.role_id == role_id)
    if task_id is not None:
        query = query.filter(Assessment.task_id == task_id)
    if parsed_from is not None:
        query = query.filter(Assessment.created_at >= parsed_from)
    if parsed_to is not None:
        query = query.filter(Assessment.created_at <= parsed_to)

    assessments = query.all()
    total = len(assessments)
    completed = [assessment for assessment in assessments if _is_completed(assessment) and assessment.completed_at]
    completed_count = len(completed)

    weekly_completion: list[dict] = []
    now = datetime.now(timezone.utc)
    for i in range(4, -1, -1):
        week_end = now - timedelta(weeks=i)
        week_start = week_end - timedelta(weeks=1)
        started_in_week = []
        completed_in_week = []
        for assessment in assessments:
            started = _ensure_utc(assessment.started_at) if assessment.started_at else None
            done = _ensure_utc(assessment.completed_at) if assessment.completed_at else None
            if started and week_start <= started < week_end:
                started_in_week.append(assessment)
                if assessment in completed and done and week_start <= done < week_end:
                    completed_in_week.append(assessment)
        started_count = len(started_in_week)
        done_count = len(completed_in_week)
        rate = round((done_count / started_count) * 100) if started_count else 0
        weekly_completion.append({"week": f"Week {5 - i}", "rate": rate, "count": done_count})

    scores_10 = [score for score in (_score_10(assessment) for assessment in completed) if score is not None]
    scores_100 = [score for score in (_score_100(assessment) for assessment in completed) if score is not None]
    top_score = max(scores_10) if scores_10 else None
    avg_score = round(sum(scores_10) / len(scores_10), 1) if scores_10 else None
    calibration_scores = [
        float(assessment.calibration_score)
        for assessment in completed
        if isinstance(assessment.calibration_score, (int, float))
    ]
    avg_calibration_score = round(sum(calibration_scores) / len(calibration_scores), 1) if calibration_scores else None

    times_min = []
    for assessment in completed:
        if assessment.started_at and assessment.completed_at:
            started = _ensure_utc(assessment.started_at)
            completed_at = _ensure_utc(assessment.completed_at)
            times_min.append((completed_at - started).total_seconds() / 60)
    avg_time_minutes = int(round(sum(times_min) / len(times_min), 0)) if times_min else None
    completion_rate = round((completed_count / total) * 100, 1) if total else 0

    unique_candidate_count = len({assessment.candidate_id for assessment in assessments if assessment.candidate_id})
    unique_task_count = len({assessment.task_id for assessment in assessments if assessment.task_id})

    return {
        "weekly_completion": weekly_completion[:5],
        "total_assessments": total,
        "total_candidates": unique_candidate_count,
        "total_tasks": unique_task_count,
        "completed_count": completed_count,
        "completion_rate": completion_rate,
        "top_score": top_score,
        "avg_score": avg_score,
        "avg_time_minutes": avg_time_minutes,
        "avg_calibration_score": avg_calibration_score,
        "score_buckets": _build_score_buckets(scores_100),
        "dimension_averages": _build_dimension_averages(completed),
    }


@router.get("/benchmarks")
def get_task_benchmarks(
    task_id: int = Query(..., gt=0),
    assessment_id: Optional[int] = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return percentile benchmarks for completed assessments on a task."""
    org_id = current_user.organization_id
    if not org_id:
        raise HTTPException(status_code=404, detail="No organization associated")

    cache_key = (org_id, task_id)
    cached = _benchmark_cache.get(cache_key)
    now_ts = _now_ts()
    if cached and cached.get("expires_at", 0) > now_ts:
        result = dict(cached["payload"])
    else:
        assessments = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id == org_id,
                Assessment.task_id == task_id,
                Assessment.status.in_(
                    [
                        AssessmentStatus.COMPLETED,
                        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                    ]
                ),
            )
            .all()
        )
        scores_100 = sorted(
            score
            for score in (_score_100(assessment) for assessment in assessments)
            if score is not None
        )
        sample_size = len(scores_100)
        if sample_size < 20:
            result = {
                "task_id": task_id,
                "sample_size": sample_size,
                "available": False,
                "message": "Not enough data for benchmarks yet (need 20+ completions)",
                "dimension_averages": _build_dimension_averages(assessments),
            }
        else:
            result = {
                "task_id": task_id,
                "sample_size": sample_size,
                "available": True,
                "p25": round(_percentile(scores_100, 0.25), 2),
                "p50": round(_percentile(scores_100, 0.50), 2),
                "p75": round(_percentile(scores_100, 0.75), 2),
                "p90": round(_percentile(scores_100, 0.90), 2),
                "dimension_averages": _build_dimension_averages(assessments),
            }
        _benchmark_cache[cache_key] = {
            "expires_at": now_ts + _BENCHMARK_CACHE_TTL_SECONDS,
            "payload": result,
        }

    if assessment_id and result.get("available"):
        assessment = (
            db.query(Assessment)
            .filter(
                Assessment.id == assessment_id,
                Assessment.organization_id == org_id,
                Assessment.task_id == task_id,
            )
            .first()
        )
        if assessment:
            completed_assessments = (
                db.query(Assessment)
                .filter(
                    Assessment.organization_id == org_id,
                    Assessment.task_id == task_id,
                    Assessment.status.in_(
                        [
                            AssessmentStatus.COMPLETED,
                            AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                        ]
                    ),
                )
                .all()
            )
            overall_distribution = [
                score
                for score in (_score_100(item) for item in completed_assessments)
                if score is not None
            ]
            candidate_score = _score_100(assessment)
            candidate_percentiles: dict[str, float] = {}
            if candidate_score is not None:
                candidate_percentiles["overall"] = _percentile_rank(overall_distribution, candidate_score)

            dimension_distributions: dict[str, list[float]] = {key: [] for key in _DIMENSION_KEYS}
            for item in completed_assessments:
                for key, value in _extract_category_scores(item).items():
                    if isinstance(value, (int, float)):
                        dimension_distributions.setdefault(key, []).append(float(value))

            for key, value in _extract_category_scores(assessment).items():
                distribution = dimension_distributions.get(key) or []
                if distribution:
                    candidate_percentiles[key] = _percentile_rank(distribution, float(value))

            result = {
                **result,
                "assessment_id": assessment.id,
                "candidate_percentiles": candidate_percentiles,
            }

    return result
