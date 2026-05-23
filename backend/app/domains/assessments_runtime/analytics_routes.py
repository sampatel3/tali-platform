"""Analytics endpoints for assessment metrics and benchmarking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session, joinedload

from ...agent_runtime import budget_guard
from ...platform.database import get_db
from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.user import User

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


def _completed_assessment_filter():
    return and_(
        Assessment.completed_at.isnot(None),
        Assessment.is_voided.is_(False),
        or_(
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.completed_due_to_timeout.is_(True),
        ),
    )


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
    taali_score = getattr(assessment, "taali_score", None)
    if isinstance(taali_score, (int, float)):
        return float(taali_score)
    assessment_score = getattr(assessment, "assessment_score", None)
    if isinstance(assessment_score, (int, float)):
        return float(assessment_score)
    final_score = getattr(assessment, "final_score", None)
    if isinstance(final_score, (int, float)):
        return float(final_score)
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score) * 10.0
    return None


def _score_10(assessment: Assessment) -> Optional[float]:
    score100 = _score_100(assessment)
    if score100 is not None:
        return score100 / 10.0
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    return None


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

    query = db.query(Assessment).filter(
        Assessment.organization_id == org_id,
        Assessment.is_voided.is_(False),
    )
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
                _completed_assessment_filter(),
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
                Assessment.is_voided.is_(False),
            )
            .first()
        )
        if assessment:
            completed_assessments = (
                db.query(Assessment)
                .filter(
                    Assessment.organization_id == org_id,
                    Assessment.task_id == task_id,
                    _completed_assessment_filter(),
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


# ----------------------------------------------------------------------------
# Reporting summary endpoint — backs the Mission Control "Your agent in
# narrative" page (HANDOFF chat.md / Tali Redesign canvas reporting tile).
# Returns everything the page renders in one round trip:
# - KPIs with deltas vs the prior equivalent window
# - Narrator paragraph with concrete numbers + clickable chip explanations
# - Decisions feed (most recent agent decisions in window)
# - Anomalies (paused agents, budget overruns, low confidence, score drift)
# - Funnel counts by pipeline stage (APPLIED → INVITED → DONE → REVIEW → HIRED)
# - Score distribution buckets (re-uses the existing decile binning)
# ----------------------------------------------------------------------------

# Pipeline stages map to the canvas's funnel rows. We display "DONE" as the
# label for `in_assessment` since the funnel narrates *what was scored*, not
# *what is in flight*; and "HIRED" as the terminal step from
# application_outcome rather than pipeline_stage. APPLIED + INVITED + DONE
# map directly from pipeline_stage; REVIEW maps to pipeline_stage="review".
_FUNNEL_STAGES = (
    ("APPLIED", "applied"),
    ("INVITED", "invited"),
    ("DONE", "in_assessment"),
    ("REVIEW", "review"),
    ("HIRED", "_hired"),
)


def _ms_format_dollars(cents: int | None) -> str:
    n = (cents or 0) / 100.0
    if n >= 100:
        return f"${int(round(n))}"
    return f"${n:.0f}"


def _decisions_in_window(
    db: Session,
    org_id: int,
    *,
    role_id: Optional[int],
    parsed_from: Optional[datetime],
    parsed_to: Optional[datetime],
) -> List[AgentDecision]:
    q = (
        db.query(AgentDecision)
        .filter(AgentDecision.organization_id == org_id)
    )
    if role_id is not None:
        q = q.filter(AgentDecision.role_id == role_id)
    if parsed_from is not None:
        q = q.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        q = q.filter(AgentDecision.created_at <= parsed_to)
    return q.order_by(desc(AgentDecision.created_at)).all()


# Decision statuses that mean the decision is still in the recruiter's
# queue and was NOT actually carried out — they must not count toward the
# "auto-advanced" / "auto-rejected" automation KPIs.
_NON_RESOLVED_DECISION_STATUSES = {"pending", "reverted_for_feedback"}


def _is_resolved_decision(decision: AgentDecision) -> bool:
    return str(decision.status or "").lower() not in _NON_RESOLVED_DECISION_STATUSES


def _decision_kind(decision: AgentDecision) -> str:
    t = str(decision.decision_type or "").lower()
    if "advance" in t:
        return "advance"
    if "reject" in t:
        return "reject"
    if "flag" in t or "borderline" in t:
        return "flag"
    if "pause" in t:
        return "pause"
    if "invite" in t:
        return "invite"
    return "action"


def _delta_pct(current: float, prior: float) -> Optional[float]:
    if prior <= 0:
        return None
    return round(((current - prior) / prior) * 100.0, 1)


@router.get("/reporting-summary")
def get_reporting_summary(
    role_id: Optional[int] = Query(default=None),
    task_id: Optional[int] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregate everything the Reporting page renders into a single payload.

    The Mission Control reporting tile expects KPIs with prior-period
    deltas, an in-narrative paragraph with chip-button drill-ins, the
    decisions feed, anomalies, the funnel, and the score histogram —
    all calibrated to the active filter window. Composing this on the
    server keeps the page render to one fetch and avoids fan-out
    requests for the org-spend rollup.
    """
    org_id = current_user.organization_id
    if not org_id:
        return {
            "window": {"from": None, "to": None, "label": "Last 30 days"},
            "kpis": {
                "decisions_made": {"current": 0, "prior": 0, "delta_pct": None},
                "auto_advanced": {"current": 0, "borderlines_flagged": 0},
                "auto_rejected": {"current": 0, "below_threshold": 0},
                "org_spend": {
                    "spent_cents": 0, "budget_cents": 0,
                    "over_pct": None, "top_role": None,
                    "active_role_count": 0,
                },
            },
            "narrator": {"paragraph": "Your agent has not seen any candidates yet.", "chips": []},
            "decisions_feed": [],
            "anomalies": [],
            "funnel": [],
            "score_buckets": _build_score_buckets([]),
        }

    parsed_from = _parse_filter_datetime(date_from, end_of_day=False)
    parsed_to = _parse_filter_datetime(date_to, end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    # Default window: last 30 days, ending now.
    now = datetime.now(timezone.utc)
    if parsed_to is None:
        parsed_to = now
    if parsed_from is None:
        parsed_from = parsed_to - timedelta(days=30)
    window_days = max(1, int((parsed_to - parsed_from).total_seconds() / 86400))
    window_label = (
        "Last 7 days" if window_days <= 7
        else "Last 30 days" if window_days <= 31
        else "Last 90 days" if window_days <= 92
        else f"Last {window_days} days"
    )
    prior_to = parsed_from
    prior_from = parsed_from - timedelta(days=window_days)

    # ── Agent decisions in window (current + prior for delta) ──────────
    decisions = _decisions_in_window(
        db, org_id, role_id=role_id, parsed_from=parsed_from, parsed_to=parsed_to,
    )
    prior_decisions = _decisions_in_window(
        db, org_id, role_id=role_id, parsed_from=prior_from, parsed_to=prior_to,
    )

    # Auto-advance / auto-reject KPIs count only decisions the agent
    # actually carried out — pending (and sent-back-for-teaching) decisions
    # are still in the recruiter's queue and must not be reported as
    # completed automations.
    auto_advanced_count = sum(
        1 for d in decisions if _is_resolved_decision(d) and _decision_kind(d) == "advance"
    )
    auto_rejected_count = sum(
        1 for d in decisions if _is_resolved_decision(d) and _decision_kind(d) == "reject"
    )
    flagged_count = sum(1 for d in decisions if _decision_kind(d) == "flag")
    paused_count = sum(1 for d in decisions if _decision_kind(d) == "pause")

    # Assessments closed in the window — counted alongside agent decisions
    # for the "Decisions made" KPI, which represents *every* consequential
    # action the agent took (CV scoring + advance/reject/flag/etc.).
    asmnt_q = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.is_voided.is_(False),
            Assessment.created_at >= parsed_from,
            Assessment.created_at <= parsed_to,
        )
    )
    if role_id is not None:
        asmnt_q = asmnt_q.filter(Assessment.role_id == role_id)
    if task_id is not None:
        asmnt_q = asmnt_q.filter(Assessment.task_id == task_id)
    assessments = asmnt_q.all()
    completed = [a for a in assessments if _is_completed(a) and a.completed_at]
    decisions_made = len(decisions) + len(assessments)
    prior_asmnt_q = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.is_voided.is_(False),
            Assessment.created_at >= prior_from,
            Assessment.created_at <= prior_to,
        )
    )
    # Apply the SAME role_id/task_id filters as the current window so the
    # delta isn't skewed (prior counted org-wide while current was scoped).
    if role_id is not None:
        prior_asmnt_q = prior_asmnt_q.filter(Assessment.role_id == role_id)
    if task_id is not None:
        prior_asmnt_q = prior_asmnt_q.filter(Assessment.task_id == task_id)
    prior_assessments = prior_asmnt_q.count()
    prior_decisions_made = len(prior_decisions) + prior_assessments

    # ── Org spend rollup across agent-enabled roles ────────────────────
    agent_roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .all()
    )
    spent_total = 0
    budget_total = 0
    role_spend: List[Tuple[Role, int]] = []
    for role in agent_roles:
        try:
            spent = budget_guard.month_to_date_spend_cents(db, role=role)
        except Exception:
            spent = 0
        spent_total += int(spent or 0)
        budget_total += int(role.monthly_usd_budget_cents or 0)
        role_spend.append((role, int(spent or 0)))

    over_pct: Optional[float] = None
    top_role_name: Optional[str] = None
    if budget_total > 0:
        over_pct = round(((spent_total - budget_total) / budget_total) * 100.0, 1)
    if role_spend:
        role_spend.sort(key=lambda r: r[1], reverse=True)
        top = role_spend[0]
        if top[1] > 0:
            top_role_name = top[0].name

    # ── Funnel counts (org-wide or role-scoped) ────────────────────────
    app_q = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org_id,
    )
    if role_id is not None:
        app_q = app_q.filter(CandidateApplication.role_id == role_id)
    applications = app_q.all()
    funnel: List[Dict[str, Any]] = []
    total_applied = len(applications)
    for label, key in _FUNNEL_STAGES:
        if key == "_hired":
            count = sum(
                1 for a in applications
                if str(a.application_outcome or "").lower() == "hired"
            )
        else:
            count = sum(
                1 for a in applications
                if str(a.pipeline_stage or "").lower() == key
            )
        pct = round((count / total_applied) * 100.0, 1) if total_applied else 0.0
        funnel.append({"label": label, "key": key, "count": count, "percentage": pct})

    # ── Decisions feed (recent N with candidate names) ─────────────────
    feed_rows = (
        db.query(AgentDecision, CandidateApplication, Candidate)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(AgentDecision.organization_id == org_id)
    )
    if role_id is not None:
        feed_rows = feed_rows.filter(AgentDecision.role_id == role_id)
    if parsed_from is not None:
        feed_rows = feed_rows.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        feed_rows = feed_rows.filter(AgentDecision.created_at <= parsed_to)
    feed_rows = feed_rows.order_by(desc(AgentDecision.created_at)).limit(30).all()

    decisions_feed: List[Dict[str, Any]] = []
    for decision, application, candidate in feed_rows:
        kind = _decision_kind(decision)
        candidate_name = (
            (getattr(candidate, "full_name", None) if candidate else None)
            or (getattr(candidate, "email", None) if candidate else None)
            or "Candidate"
        )
        decisions_feed.append({
            "id": int(decision.id),
            "kind": kind,
            "decision_type": decision.decision_type,
            "recommendation": decision.recommendation,
            "reasoning": decision.reasoning,
            "confidence": float(decision.confidence) if decision.confidence is not None else None,
            "status": decision.status,
            "candidate_name": candidate_name,
            "application_id": int(decision.application_id),
            "role_id": int(decision.role_id),
            "created_at": decision.created_at,
        })

    # ── Anomalies ──────────────────────────────────────────────────────
    anomalies: List[Dict[str, Any]] = []
    paused_roles = [
        r for r in agent_roles
        if getattr(r, "agent_paused_at", None) is not None
    ]
    for role in paused_roles[:3]:
        anomalies.append({
            "tone": "amber",
            "title": f"Paused agent on {role.name}",
            "body": (role.agent_paused_reason
                     or "Agent is paused on this role. Resume from the role pipeline page."),
        })
    if budget_total > 0 and spent_total > budget_total:
        spent_label = _ms_format_dollars(spent_total)
        budget_label = _ms_format_dollars(budget_total)
        driver = f"driven by {top_role_name}" if top_role_name else ""
        anomalies.append({
            "tone": "red",
            "title": "Spend over cap (org-wide)",
            "body": f"{spent_label} of {budget_label}. " + (f"Staff ML drove most of it ({driver}). " if driver else "")
                    + "Review per-role caps in Settings → AI tooling.",
        })
    low_conf = [
        d for d in decisions
        if d.confidence is not None and float(d.confidence) < 0.55
    ]
    if len(low_conf) >= 3:
        anomalies.append({
            "tone": "amber",
            "title": f"Confidence low on {len(low_conf)} decisions",
            "body": "Coverage below 55%. Recommend uploading 3+ exemplar reviews so the agent can calibrate.",
        })
    if not anomalies and total_applied > 0:
        anomalies.append({
            "tone": "neutral",
            "title": "Nothing flagged",
            "body": "No paused agents, no budget overruns, confidence holding steady. Your agent is having a quiet day.",
        })

    # ── Score buckets (existing helper) ────────────────────────────────
    scores_100 = [s for s in (_score_100(a) for a in completed) if s is not None]
    score_buckets = _build_score_buckets(scores_100)

    # ── Narrator paragraph + drill-in chips ────────────────────────────
    if decisions_made == 0 and not decisions_feed:
        narrator_paragraph = (
            f"No assessments closed in the {window_label.lower()}. "
            "Once candidates start completing tasks, the agent will narrate "
            "what it advanced, rejected, and flagged."
        )
        chips: List[Dict[str, str]] = []
    else:
        bits = []
        bits.append(
            f"In the {window_label.lower()}, your agent acted "
            f"{decisions_made} time{'s' if decisions_made != 1 else ''}."
        )
        if auto_advanced_count or auto_rejected_count:
            bits.append(
                f"It auto-advanced {auto_advanced_count} candidate"
                f"{'s' if auto_advanced_count != 1 else ''} "
                f"and auto-rejected {auto_rejected_count} below threshold."
            )
        if flagged_count:
            bits.append(
                f"It flagged {flagged_count} borderline candidate"
                f"{'s' if flagged_count != 1 else ''} for your judgment."
            )
        if budget_total > 0:
            bits.append(
                f"Spend sat at {_ms_format_dollars(spent_total)} of "
                f"{_ms_format_dollars(budget_total)} cap"
                + (f" — {over_pct:.0f}% over." if over_pct and over_pct > 0 else ".")
            )
        if paused_count:
            bits.append(f"It paused itself {paused_count} time{'s' if paused_count != 1 else ''} when signal got thin.")
        narrator_paragraph = " ".join(bits)

        chips = []
        if budget_total > 0 and spent_total > budget_total:
            chips.append({
                "key": "over_budget",
                "label": "Why over budget?",
                "body": (
                    f"Org spend is {_ms_format_dollars(spent_total)} against a "
                    f"{_ms_format_dollars(budget_total)} cap"
                    + (f" — {top_role_name} drove most of it." if top_role_name else ".")
                ),
            })
        if flagged_count:
            chips.append({
                "key": "borderlines",
                "label": f"Show the {flagged_count} borderline{'s' if flagged_count != 1 else ''}",
                "body": "Borderline candidates are listed in the decisions feed below — filter on `flag` to jump straight to them.",
            })
        if paused_roles:
            first = paused_roles[0]
            chips.append({
                "key": "paused",
                "label": f"Why was {first.name} paused?",
                "body": (
                    first.agent_paused_reason
                    or "The agent paused itself when confidence dropped below threshold. Resume from the role pipeline page."
                ),
            })

    return {
        "window": {
            "from": parsed_from.isoformat() if parsed_from else None,
            "to": parsed_to.isoformat() if parsed_to else None,
            "label": window_label,
        },
        "kpis": {
            "decisions_made": {
                "current": decisions_made,
                "prior": prior_decisions_made,
                "delta_pct": _delta_pct(decisions_made, prior_decisions_made),
            },
            "auto_advanced": {
                "current": auto_advanced_count,
                "borderlines_flagged": flagged_count,
            },
            "auto_rejected": {
                "current": auto_rejected_count,
                "below_threshold": auto_rejected_count,
            },
            "org_spend": {
                "spent_cents": spent_total,
                "budget_cents": budget_total,
                "over_pct": over_pct,
                "top_role": top_role_name,
                "active_role_count": len(agent_roles),
            },
        },
        "narrator": {"paragraph": narrator_paragraph, "chips": chips},
        "decisions_feed": decisions_feed,
        "anomalies": anomalies,
        "funnel": funnel,
        "score_buckets": score_buckets,
    }
