"""Single-scan SQL aggregates for the base analytics endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, extract, func, or_
from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus

DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
    "task_completion": ("task_completion",),
    "prompt_clarity": ("prompt_clarity",),
    "context_provision": ("context_provision",),
    "independence_efficiency": ("independence_efficiency", "independence"),
    "response_utilization": ("response_utilization", "utilization"),
    "debugging_design": ("debugging_design", "approach"),
    "written_communication": ("written_communication", "communication"),
    "role_fit": ("role_fit", "cv_match"),
}

SCORE_RANGES = (
    ("0-20", 0, 20),
    ("20-40", 20, 40),
    ("40-60", 40, 60),
    ("60-80", 60, 80),
    ("80-100", 80, 101),
)


def _dimension_value(key: str):
    aliases = DIMENSION_ALIASES[key]
    roots = (
        Assessment.score_breakdown["category_scores"],
        Assessment.prompt_analytics["category_scores"],
        Assessment.prompt_analytics["detailed_scores"]["category_scores"],
    )
    return func.coalesce(
        *(root[alias].as_float() for root in roots for alias in aliases)
    )


def _score_100_expression():
    raw = case(
        (Assessment.taali_score.is_not(None), Assessment.taali_score),
        (Assessment.assessment_score.is_not(None), Assessment.assessment_score),
        (Assessment.final_score.is_not(None), Assessment.final_score),
        else_=Assessment.score * 10.0,
    )
    clamped = case(
        (raw < 0, None),
        (raw > 100, 100.0),
        else_=raw,
    )
    return func.round(clamped, 1)


def _duration_minutes_expression(db: Session):
    if db.get_bind().dialect.name == "sqlite":
        return (
            func.julianday(Assessment.completed_at)
            - func.julianday(Assessment.started_at)
        ) * 1440.0
    return extract(
        "epoch", Assessment.completed_at - Assessment.started_at
    ) / 60.0


def get_base_analytics_summary(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None = None,
    task_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute all-time and rolling analytics without hydrating history rows."""
    filters = [
        Assessment.organization_id == organization_id,
        Assessment.is_voided.is_(False),
    ]
    if role_id is not None:
        filters.append(Assessment.role_id == role_id)
    if task_id is not None:
        filters.append(Assessment.task_id == task_id)
    if date_from is not None:
        filters.append(Assessment.created_at >= date_from)
    if date_to is not None:
        filters.append(Assessment.created_at <= date_to)

    completed = and_(
        Assessment.completed_at.is_not(None),
        or_(
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        ),
    )
    timed_out = and_(
        completed,
        Assessment.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    )
    score_100 = _score_100_expression()
    scored = and_(completed, score_100.is_not(None))
    duration = _duration_minutes_expression(db)

    columns = [
        func.count(Assessment.id).label("total"),
        func.count(func.distinct(Assessment.candidate_id)).label("candidates"),
        func.count(func.distinct(Assessment.task_id)).label("tasks"),
        func.sum(case((completed, 1), else_=0)).label("completed"),
        func.sum(case((timed_out, 1), else_=0)).label("timed_out"),
        func.max(case((scored, score_100 / 10.0), else_=None)).label("top_score"),
        func.avg(case((scored, score_100 / 10.0), else_=None)).label("avg_score"),
        func.avg(
            case(
                (and_(completed, Assessment.started_at.is_not(None)), duration),
                else_=None,
            )
        ).label("avg_time"),
        func.count(case((scored, 1), else_=None)).label("score_count"),
    ]

    for label, start, end in SCORE_RANGES:
        columns.append(
            func.sum(
                case(
                    (and_(scored, score_100 >= start, score_100 < end), 1),
                    else_=0,
                )
            ).label(f"bucket_{label.replace('-', '_')}")
        )
    for key in DIMENSION_ALIASES:
        columns.append(
            func.avg(
                case(
                    (completed, func.round(_dimension_value(key), 2)),
                    else_=None,
                )
            ).label(f"dimension_{key}")
        )

    current = now or datetime.now(timezone.utc)
    weeks: list[tuple[datetime, datetime]] = []
    for index in range(4, -1, -1):
        week_end = current - timedelta(weeks=index)
        week_start = week_end - timedelta(weeks=1)
        weeks.append((week_start, week_end))
        started_in_week = and_(
            Assessment.started_at >= week_start,
            Assessment.started_at < week_end,
        )
        completed_in_week = and_(
            completed,
            started_in_week,
            Assessment.completed_at >= week_start,
            Assessment.completed_at < week_end,
        )
        columns.extend((
            func.sum(case((started_in_week, 1), else_=0)).label(f"week_started_{index}"),
            func.sum(case((completed_in_week, 1), else_=0)).label(f"week_done_{index}"),
        ))

    values = db.query(*columns).filter(*filters).one()._mapping
    total = int(values["total"] or 0)
    completed_count = int(values["completed"] or 0)
    score_count = int(values["score_count"] or 0)
    buckets = []
    for label, _start, _end in SCORE_RANGES:
        count = int(values[f"bucket_{label.replace('-', '_')}"] or 0)
        buckets.append({
            "range": label,
            "count": count,
            "percentage": round((count / score_count) * 100, 1) if score_count else 0.0,
        })
    weekly = []
    for display_index, source_index in enumerate(range(4, -1, -1), start=1):
        started_count = int(values[f"week_started_{source_index}"] or 0)
        done_count = int(values[f"week_done_{source_index}"] or 0)
        weekly.append({
            "week": f"Week {display_index}",
            "rate": round((done_count / started_count) * 100) if started_count else 0,
            "count": done_count,
        })
    avg_time = values["avg_time"]
    return {
        "weekly_completion": weekly,
        "total_assessments": total,
        "total_candidates": int(values["candidates"] or 0),
        "total_tasks": int(values["tasks"] or 0),
        "completed_count": completed_count,
        "timed_out_count": int(values["timed_out"] or 0),
        "completion_rate": round((completed_count / total) * 100, 1) if total else 0,
        "top_score": float(values["top_score"]) if values["top_score"] is not None else None,
        "avg_score": round(float(values["avg_score"]), 1) if values["avg_score"] is not None else None,
        "avg_time_minutes": int(round(float(avg_time), 0)) if avg_time is not None else None,
        "score_buckets": buckets,
        "dimension_averages": {
            key: round(float(values[f"dimension_{key}"]), 2)
            if values[f"dimension_{key}"] is not None
            else None
            for key in DIMENSION_ALIASES
        },
    }


__all__ = ["get_base_analytics_summary"]
