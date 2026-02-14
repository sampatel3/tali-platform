"""Analytics endpoints for assessment metrics."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...platform.database import get_db
from ...deps import get_current_user
from ...models.user import User
from ...models.assessment import Assessment, AssessmentStatus

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def _ensure_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/")
def get_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return analytics for the current user's organization: completion rate by week, totals, top score, avg time."""
    org_id = current_user.organization_id
    if not org_id:
        return {
            "weekly_completion": [],
            "total_assessments": 0,
            "completed_count": 0,
            "completion_rate": 0,
            "top_score": None,
            "avg_score": None,
            "avg_time_minutes": None,
            "avg_calibration_score": None,
        }

    assessments = (
        db.query(Assessment)
        .filter(Assessment.organization_id == org_id)
        .all()
    )
    total = len(assessments)
    completed = [
        a for a in assessments
        if (a.status == AssessmentStatus.COMPLETED or getattr(a.status, "value", a.status) == "completed")
        and a.completed_at
    ]
    completed_count = len(completed)

    # Last 5 weeks: completion rate per week (completed in week / started in week)
    weekly_completion = []
    now = datetime.now(timezone.utc)
    for i in range(4, -1, -1):
        week_end = now - timedelta(weeks=i)
        week_start = week_end - timedelta(weeks=1)
        started_in_week = []
        completed_in_week = []
        for a in assessments:
            started = _ensure_utc(a.started_at) if a.started_at else None
            done = _ensure_utc(a.completed_at) if a.completed_at else None
            if started and week_start <= started < week_end:
                started_in_week.append(a)
                if a in completed and done and week_start <= done < week_end:
                    completed_in_week.append(a)
        denom = len(started_in_week) or 1
        num = len(completed_in_week)
        rate = round((num / denom) * 100) if denom else 0
        weekly_completion.append({"week": f"Week {5 - i}", "rate": rate, "count": num})
    if len(weekly_completion) < 5:
        while len(weekly_completion) < 5:
            weekly_completion.append({"week": f"Week {len(weekly_completion) + 1}", "rate": 0, "count": 0})

    scores = [a.score for a in completed if a.score is not None]
    top_score = max(scores) if scores else None
    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    calibration_scores = [a.calibration_score for a in completed if a.calibration_score is not None]
    avg_calibration_score = round(sum(calibration_scores) / len(calibration_scores), 1) if calibration_scores else None

    times_min = []
    for a in completed:
        if a.started_at and a.completed_at:
            start = _ensure_utc(a.started_at)
            end = _ensure_utc(a.completed_at)
            times_min.append((end - start).total_seconds() / 60)
    avg_time_minutes = int(round(sum(times_min) / len(times_min), 0)) if times_min else None
    completion_rate = round((completed_count / total) * 100, 1) if total else 0

    return {
        "weekly_completion": weekly_completion[:5],
        "total_assessments": total,
        "completed_count": completed_count,
        "completion_rate": completion_rate,
        "top_score": top_score,
        "avg_score": avg_score,
        "avg_time_minutes": avg_time_minutes,
        "avg_calibration_score": avg_calibration_score,
    }
