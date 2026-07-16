"""Analytics endpoints for assessment metrics and benchmarking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session, selectinload

from ...agent_runtime import budget_guard
from ...platform.database import get_db
from ...deps import get_current_user
from ...domains.agentic._hub_shared import open_needs_input_filter, pending_filter
from ...models.agent_decision import AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.assessment import Assessment
from ...components.scoring.assessment_metrics import (
    completed_assessment_filter as _completed_assessment_filter,
    is_completed as _is_completed,
    percentile_rank as _percentile_rank,
    score_100 as _score_100,
    extract_category_scores as _extract_category_scores,
)
from ...models.assessment_experiment import AssessmentExperiment
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.usage_event import UsageEvent
from ...models.user import User
from .pipeline_service import (
    FUNNEL_BUCKETS,
    funnel_bucket_for,
    normalize_pipeline_key,
    _post_handover_sql,
)
from .base_analytics_queries import get_base_analytics_summary

router = APIRouter(prefix="/analytics", tags=["Analytics"])

_BENCHMARK_CACHE_TTL_SECONDS = 3600
_benchmark_cache: dict[Tuple[int, int], dict] = {}

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
            "timed_out_count": 0,
            "completion_rate": 0,
            "top_score": None,
            "avg_score": None,
            "avg_time_minutes": None,
            "score_buckets": _build_score_buckets([]),
            "dimension_averages": _build_dimension_averages([]),
        }

    parsed_from = _parse_filter_datetime(date_from, end_of_day=False)
    parsed_to = _parse_filter_datetime(date_to, end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    return get_base_analytics_summary(
        db,
        organization_id=org_id,
        role_id=role_id,
        task_id=task_id,
        date_from=parsed_from,
        date_to=parsed_to,
    )


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

# Canonical funnel stages (locked Option B2) — the SAME vocabulary and
# bucketing every other funnel surface uses (shared/metrics.js
# PIPELINE_FUNNEL_STAGES and pipeline_service.role_pipeline_counts). Analytics
# previously invented its own APPLIED/INVITED/DONE/REVIEW/HIRED labels, so the
# same org saw contradictory funnels on Home vs Analytics. Labels here are the
# display strings; bucket keys come from funnel_bucket_for().
_FUNNEL_STAGE_LABELS = (
    ("applied", "Applied"),
    ("scored", "Scored"),
    ("invited", "Invited"),
    ("completed", "Completed"),
    ("advanced", "Advanced"),
    ("rejected", "Rejected"),
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
) -> List[Any]:
    # Select only the scalar columns the KPI aggregation reads — the full
    # AgentDecision row carries reasoning (Text) + several JSON blobs
    # (evidence, token_spend, input_fingerprint …) that this endpoint never
    # touches. The returned Rows still expose .decision_type/.status/
    # .human_disposition/.confidence for the helpers below.
    q = (
        db.query(
            AgentDecision.decision_type,
            AgentDecision.status,
            AgentDecision.human_disposition,
            AgentDecision.confidence,
        )
        .filter(AgentDecision.organization_id == org_id)
    )
    if role_id is not None:
        q = q.filter(AgentDecision.role_id == role_id)
    if parsed_from is not None:
        q = q.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        q = q.filter(AgentDecision.created_at <= parsed_to)
    return q.order_by(desc(AgentDecision.created_at)).all()


def _count_decisions_in_window(
    db: Session,
    org_id: int,
    *,
    role_id: Optional[int],
    parsed_from: Optional[datetime],
    parsed_to: Optional[datetime],
) -> int:
    q = db.query(func.count(AgentDecision.id)).filter(
        AgentDecision.organization_id == org_id
    )
    if role_id is not None:
        q = q.filter(AgentDecision.role_id == role_id)
    if parsed_from is not None:
        q = q.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        q = q.filter(AgentDecision.created_at <= parsed_to)
    return int(q.scalar() or 0)


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
                "human_review": {
                    "resolved": 0, "approved": 0, "overridden": 0, "taught": 0,
                    "override_rate_pct": 0.0, "teach_rate_pct": 0.0,
                },
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
    prior_decisions_count = _count_decisions_in_window(
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

    # Human-review KPIs (the HITL loop): of the resolved decisions in the
    # window, how many did the recruiter approve / override / teach. Drives
    # the Monitoring summary band's trust signal. Rates are over resolved
    # decisions (pending/sent-back rows aren't a verdict yet).
    resolved_decisions = [d for d in decisions if _is_resolved_decision(d)]
    resolved_count = len(resolved_decisions)
    approved_count = sum(1 for d in resolved_decisions if str(d.status or "").lower() == "approved")
    overridden_count = sum(1 for d in resolved_decisions if str(d.status or "").lower() == "overridden")
    taught_count = sum(
        1 for d in decisions if str(getattr(d, "human_disposition", "") or "").lower() == "taught"
    )
    override_rate_pct = round((overridden_count / resolved_count) * 100.0, 1) if resolved_count else 0.0
    teach_rate_pct = round((taught_count / resolved_count) * 100.0, 1) if resolved_count else 0.0
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
    prior_decisions_made = prior_decisions_count + prior_assessments

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
    # One batched GROUP BY for MTD spend across the org (same helper the Hub
    # uses) instead of a per-role month_to_date_spend_cents N+1.
    spend_map = budget_guard.spend_by_role_map(db, organization_id=org_id)
    spent_total = 0
    budget_total = 0
    role_spend: List[Tuple[Role, int]] = []
    for role in agent_roles:
        spent = int(spend_map.get(role.id, 0) or 0)
        spent_total += spent
        budget_total += int(role.monthly_usd_budget_cents or 0)
        role_spend.append((role, spent))

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
    # One GROUP BY over pipeline_stage + a single hired count, instead of
    # hydrating every CandidateApplication row (cv_text etc.) just to bucket
    # them in Python. total_applied is every application in scope.
    # Canonical bucketing: mirror role_pipeline_counts so Analytics agrees with
    # Home/Jobs. "Scored" = evaluated (real cv_match score OR a genuine
    # pre-screen run); post-handover candidates (advanced in Workable) roll into
    # "advanced"; "rejected" is an application_outcome counted separately.
    scored_expr = or_(
        CandidateApplication.cv_match_score.isnot(None),
        and_(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
        ),
    )
    ph_expr = _post_handover_sql()
    funnel_base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org_id,
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "open",
    )
    if role_id is not None:
        funnel_base = funnel_base.filter(CandidateApplication.role_id == role_id)
    bucket_rows = (
        funnel_base.with_entities(
            CandidateApplication.pipeline_stage,
            scored_expr,
            ph_expr,
            func.count(CandidateApplication.id),
        )
        .group_by(CandidateApplication.pipeline_stage, scored_expr, ph_expr)
        .all()
    )
    bucket_counts = {bucket: 0 for bucket in FUNNEL_BUCKETS}
    for stage, is_scored, is_post_handover, total in bucket_rows:
        n = int(total or 0)
        if is_post_handover:
            bucket_counts["advanced"] += n
            continue
        bucket = funnel_bucket_for(normalize_pipeline_key(stage), bool(is_scored))
        if bucket:
            bucket_counts[bucket] += n
    # rejected is orthogonal to pipeline_stage (an outcome), counted across all
    # stages — same as role_pipeline_counts.
    rejected_base = db.query(func.count(CandidateApplication.id)).filter(
        CandidateApplication.organization_id == org_id,
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "rejected",
    )
    if role_id is not None:
        rejected_base = rejected_base.filter(CandidateApplication.role_id == role_id)
    bucket_counts["rejected"] = int(rejected_base.scalar() or 0)

    # Percentage is share-of-applied-cohort; "applied" here is the total across
    # active buckets (everyone who entered the pipeline).
    total_applied = sum(bucket_counts[b] for b in FUNNEL_BUCKETS if b != "rejected")
    funnel: List[Dict[str, Any]] = []
    for key, label in _FUNNEL_STAGE_LABELS:
        count = bucket_counts.get(key, 0)
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
            "human_review": {
                "resolved": resolved_count,
                "approved": approved_count,
                "overridden": overridden_count,
                "taught": taught_count,
                "override_rate_pct": override_rate_pct,
                "teach_rate_pct": teach_rate_pct,
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


# ----------------------------------------------------------------------------
# Cost-per-outcome — unit economics that sit next to the Outcomes funnel.
# Answers "what does our agent spend buy, per funnel unit" using the same
# BILLED-spend basis the Fleet tab shows (credits_charged, never raw Anthropic
# cost / model names). Two kinds, kept distinct exactly like
# scripts/cost_per_outcome.py:
#   DIRECT  (pre-screen, score) = billed spend on THAT feature ÷ the candidates
#           it billed for, in-window. Cost + count share one source
#           (usage_events) so they reconcile by construction.
#   LOADED  (advanced, hire)    = TOTAL billed spend in the window ÷ the
#           timestamped stage/outcome transitions in the window — advancing and
#           hiring burn no tokens themselves, so this amortises ALL spend over
#           the funnel result (the classic "$ per hire").
# Org-scoped + authed; role/window scoped to match the Analytics controls.
# ----------------------------------------------------------------------------


@router.get("/cost-per-outcome")
def get_cost_per_outcome(
    role_id: Optional[int] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """BILLED spend per funnel outcome over the selected window.

    Per-unit is ``null`` (never a crash) when the denominator count is 0.
    """
    org_id = current_user.organization_id
    parsed_from = _parse_filter_datetime(date_from, end_of_day=False)
    parsed_to = _parse_filter_datetime(date_to, end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")
    window_label = "All time"
    if parsed_from is not None:
        days = max(1, int(((parsed_to or datetime.now(timezone.utc)) - parsed_from).total_seconds() / 86400))
        window_label = f"Last {days} days"
    window = {
        "label": window_label,
        "from": parsed_from.isoformat() if parsed_from else None,
        "to": parsed_to.isoformat() if parsed_to else None,
    }

    def _unit(cost_micro: int, count: int) -> Optional[float]:
        # Per-unit BILLED cost in CENTS (fractional). None when count == 0 so a
        # division-by-zero surfaces as an empty state, not a 500. 1c = 10_000 micro.
        if not count:
            return None
        return round((int(cost_micro or 0) / 10_000) / count, 4)

    empty = {
        "window": window,
        "currency": "usd_cents",
        "billed_spend_cents": 0,
        "counts": {"pre_screened": 0, "scored": 0, "advanced": 0, "hired": 0},
        "per_outcome": {
            "pre_screen": {"cost_cents": None, "count": 0},
            "score": {"cost_cents": None, "count": 0},
            "advanced": {"cost_cents": None, "count": 0},
            "hired": {"cost_cents": None, "count": 0},
        },
    }
    if not org_id:
        return empty

    # ── BILLED spend + billed-for candidate count, by feature ──────────────
    # One GROUP BY over usage_events. credits_charged is the marked-up billed
    # figure the cap is denominated in; distinct entity_id is the candidates the
    # feature actually billed for (the direct-cost denominator).
    spend_q = db.query(
        UsageEvent.feature,
        func.coalesce(func.sum(UsageEvent.credits_charged), 0),
        func.count(func.distinct(UsageEvent.entity_id)),
    ).filter(UsageEvent.organization_id == org_id)
    if role_id is not None:
        spend_q = spend_q.filter(UsageEvent.role_id == role_id)
    if parsed_from is not None:
        spend_q = spend_q.filter(UsageEvent.created_at >= parsed_from)
    if parsed_to is not None:
        spend_q = spend_q.filter(UsageEvent.created_at <= parsed_to)
    spend_rows = spend_q.group_by(UsageEvent.feature).all()

    total_micro = 0
    feat_micro: Dict[str, int] = {}
    feat_entities: Dict[str, int] = {}
    for feature, micro, entities in spend_rows:
        micro = int(micro or 0)
        total_micro += micro
        feat_micro[str(feature)] = micro
        feat_entities[str(feature)] = int(entities or 0)

    pre_micro = feat_micro.get("prescreen", 0)
    score_micro = feat_micro.get("score", 0)
    pre_n = feat_entities.get("prescreen", 0)
    score_n = feat_entities.get("score", 0)

    # ── Fully-loaded outcomes — timestamped transitions in-window ──────────
    # Distinct application_id on the transition events (a candidate that flips to
    # 'advanced' twice counts once). Constant two queries — never per-role N+1.
    def _transition_count(event_type: str, column, value: str) -> int:
        q = db.query(
            func.count(func.distinct(CandidateApplicationEvent.application_id))
        ).filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplicationEvent.event_type == event_type,
            column == value,
        )
        if role_id is not None:
            q = q.join(
                CandidateApplication,
                CandidateApplication.id == CandidateApplicationEvent.application_id,
            ).filter(CandidateApplication.role_id == role_id)
        if parsed_from is not None:
            q = q.filter(CandidateApplicationEvent.created_at >= parsed_from)
        if parsed_to is not None:
            q = q.filter(CandidateApplicationEvent.created_at <= parsed_to)
        return int(q.scalar() or 0)

    advanced = _transition_count("pipeline_stage_changed", CandidateApplicationEvent.to_stage, "advanced")
    hired = _transition_count("application_outcome_changed", CandidateApplicationEvent.to_outcome, "hired")

    return {
        "window": window,
        "currency": "usd_cents",
        "billed_spend_cents": budget_guard.micro_to_cents(total_micro),
        "counts": {
            "pre_screened": pre_n,
            "scored": score_n,
            "advanced": advanced,
            "hired": hired,
        },
        "per_outcome": {
            # DIRECT: feature billed spend ÷ candidates the feature billed for.
            "pre_screen": {"cost_cents": _unit(pre_micro, pre_n), "count": pre_n},
            "score": {"cost_cents": _unit(score_micro, score_n), "count": score_n},
            # FULLY-LOADED: total window billed spend ÷ outcomes in window.
            "advanced": {"cost_cents": _unit(total_micro, advanced), "count": advanced},
            "hired": {"cost_cents": _unit(total_micro, hired), "count": hired},
        },
    }


# ----------------------------------------------------------------------------
# Decisions & outcomes by role — backs the by-role breakdown the Hub renders
# inside the "Score distribution & funnel" accordion. All-time (cumulative),
# because the questions it answers are conversion questions:
#   a) what decisions were made + approved, per role
#   b) where the candidates Tali advanced now sit in Workable (live snapshot)
#   c) how many advance decisions reached final interview / offer / hired
# Workable-stage placement is always a *current* snapshot regardless of when
# the decision was made. "Reached X or beyond" is computed monotonically off
# the current normalized stage (stages are roughly ordered), so e.g. a
# candidate now at "offer" also counts toward "reached final interview".
# ----------------------------------------------------------------------------

# Only the explicit "advance" verdict feeds the conversion view.
_ADVANCE_DECISION_TYPES = {"advance_to_interview"}
# Statuses that mean the recruiter accepted the agent's recommendation and it
# was carried out. ``processing`` is the brief in-flight state right after an
# approve while the Workable writeback dispatches.
_APPROVED_DECISION_STATUSES = {"approved", "processing"}
# Normalized Workable stages (see pipeline_service.POST_HANDOVER_WORKABLE_STAGES).
_FINAL_INTERVIEW_NORM_STAGES = {"final_interview"}
_OFFER_NORM_STAGES = {"offer", "offer_extended", "offer_accepted"}
_HIRED_NORM_STAGES = {"hired"}
_REJECT_OUTCOMES = {"rejected", "withdrawn"}


def _empty_decisions_bucket() -> Dict[str, Any]:
    return {"total": 0, "approved": 0, "by_type": {}}


def _empty_conversion_bucket() -> Dict[str, Any]:
    return {
        "advanced_total": 0,
        "reached_final_interview": 0,
        "reached_offer": 0,
        "hired": 0,
        "rejected": 0,
        # Current Workable stage of the advanced cohort — lets the UI explain
        # an "Advanced 40 / reached final 0" gap (e.g. "36 still at technical
        # interview") instead of looking like a bug.
        "by_stage": {},
    }


def _score_summary(values: Sequence[float]) -> Dict[str, Any]:
    vals = sorted(float(v) for v in values if isinstance(v, (int, float)))
    n = len(vals)
    if n == 0:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None, "p25": None, "p75": None}
    return {
        "count": n,
        "avg": round(sum(vals) / n, 1),
        "median": round(_percentile(vals, 0.50), 1),
        "min": round(vals[0], 1),
        "max": round(vals[-1], 1),
        "p25": round(_percentile(vals, 0.25), 1),
        "p75": round(_percentile(vals, 0.75), 1),
    }


@router.get("/decisions-breakdown")
def get_decisions_breakdown(
    role_id: Optional[int] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Decisions + Workable-stage outcomes grouped by role.

    Decision counts and the advance cohort honour the optional role/time
    window; the Workable-stage mix and score stats stay a *current* snapshot
    (scoped to the role when given) — "decisions made in this window, where
    those candidates sit now".
    """
    org_id = current_user.organization_id
    parsed_from = _parse_filter_datetime(date_from, end_of_day=False)
    parsed_to = _parse_filter_datetime(date_to, end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")
    window_label = "All time"
    if parsed_from is not None:
        days = max(1, int(((parsed_to or datetime.now(timezone.utc)) - parsed_from).total_seconds() / 86400))
        window_label = f"Last {days} days"
    window = {
        "label": window_label,
        "from": parsed_from.isoformat() if parsed_from else None,
        "to": parsed_to.isoformat() if parsed_to else None,
    }
    empty = {
        "window": window,
        "score_basis": "taali",
        "totals": {
            "decisions": _empty_decisions_bucket(),
            "workable_stages": {},
            "advance_conversion": _empty_conversion_bucket(),
            "score_stats": _score_summary([]),
        },
        "roles": [],
    }
    if not org_id:
        return empty

    def _scope_decisions(q):
        q = q.filter(AgentDecision.organization_id == org_id)
        if role_id is not None:
            q = q.filter(AgentDecision.role_id == role_id)
        if parsed_from is not None:
            q = q.filter(AgentDecision.created_at >= parsed_from)
        if parsed_to is not None:
            q = q.filter(AgentDecision.created_at <= parsed_to)
        return q

    def _scope_apps(q):
        q = q.filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        if role_id is not None:
            q = q.filter(CandidateApplication.role_id == role_id)
        return q

    # ── a) Decisions grouped by role × type × status ───────────────────
    decision_rows = _scope_decisions(
        db.query(
            AgentDecision.role_id,
            AgentDecision.decision_type,
            AgentDecision.status,
            func.count(AgentDecision.id),
        )
    ).group_by(AgentDecision.role_id, AgentDecision.decision_type, AgentDecision.status).all()
    decisions_by_role: Dict[int, Dict[str, Any]] = {}
    totals_decisions = _empty_decisions_bucket()
    for rid, dtype, status, count in decision_rows:
        if rid is None:
            continue
        count = int(count or 0)
        type_key = str(dtype or "unknown")
        is_approved = str(status or "").lower() in _APPROVED_DECISION_STATUSES
        for bucket in (decisions_by_role.setdefault(rid, _empty_decisions_bucket()), totals_decisions):
            bucket["total"] += count
            type_bucket = bucket["by_type"].setdefault(type_key, {"total": 0, "approved": 0})
            type_bucket["total"] += count
            if is_approved:
                bucket["approved"] += count
                type_bucket["approved"] += count

    # ── Simple Workable-stage counts per role (all live applications) ───
    stage_rows = _scope_apps(
        db.query(
            CandidateApplication.role_id,
            CandidateApplication.external_stage_normalized,
            CandidateApplication.workable_stage,
            func.count(CandidateApplication.id),
        )
    ).group_by(
        CandidateApplication.role_id,
        CandidateApplication.external_stage_normalized,
        CandidateApplication.workable_stage,
    ).all()
    stages_by_role: Dict[int, Dict[str, int]] = {}
    totals_stages: Dict[str, int] = {}
    for rid, norm_stage, raw_stage, count in stage_rows:
        if rid is None:
            continue
        count = int(count or 0)
        key = normalize_pipeline_key(norm_stage or raw_stage) or "unstaged"
        role_stages = stages_by_role.setdefault(rid, {})
        role_stages[key] = role_stages.get(key, 0) + count
        totals_stages[key] = totals_stages.get(key, 0) + count

    # ── b/c) Advance conversion — current stage of advanced candidates ──
    # Join (not subquery) and select only application columns + id, then
    # ``distinct`` collapses an application that has several approved advance
    # decisions down to a single row (id keeps distinct apps separate).
    advance_q = (
        db.query(
            CandidateApplication.id,
            CandidateApplication.role_id,
            CandidateApplication.external_stage_normalized,
            CandidateApplication.workable_stage,
            CandidateApplication.application_outcome,
            CandidateApplication.workable_disqualified,
        )
        .join(AgentDecision, AgentDecision.application_id == CandidateApplication.id)
        .filter(
            CandidateApplication.organization_id == org_id,
            AgentDecision.decision_type.in_(_ADVANCE_DECISION_TYPES),
            AgentDecision.status.in_(_APPROVED_DECISION_STATUSES),
        )
    )
    if role_id is not None:
        advance_q = advance_q.filter(AgentDecision.role_id == role_id)
    if parsed_from is not None:
        advance_q = advance_q.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        advance_q = advance_q.filter(AgentDecision.created_at <= parsed_to)
    advance_apps = advance_q.distinct().all()
    conversion_by_role: Dict[int, Dict[str, int]] = {}
    totals_conversion = _empty_conversion_bucket()
    for _app_id, rid, norm_stage, raw_stage, outcome, disqualified in advance_apps:
        if rid is None:
            continue
        stage = normalize_pipeline_key(norm_stage or raw_stage)
        oc = str(outcome or "").lower()
        is_hired = oc == "hired" or stage in _HIRED_NORM_STAGES
        reached_offer = is_hired or stage in _OFFER_NORM_STAGES
        reached_final = reached_offer or stage in _FINAL_INTERVIEW_NORM_STAGES
        is_rejected = oc in _REJECT_OUTCOMES or bool(disqualified)
        stage_key = stage or "unstaged"
        for bucket in (conversion_by_role.setdefault(rid, _empty_conversion_bucket()), totals_conversion):
            bucket["advanced_total"] += 1
            if reached_final:
                bucket["reached_final_interview"] += 1
            if reached_offer:
                bucket["reached_offer"] += 1
            if is_hired:
                bucket["hired"] += 1
            if is_rejected:
                bucket["rejected"] += 1
            bucket["by_stage"][stage_key] = bucket["by_stage"].get(stage_key, 0) + 1

    # ── Headline score values (taali cache, falling back to cv_match) ──
    headline_score = func.coalesce(
        CandidateApplication.taali_score_cache_100,
        CandidateApplication.cv_match_score,
    )
    score_rows = _scope_apps(
        db.query(CandidateApplication.role_id, headline_score)
    ).filter(headline_score.isnot(None)).all()
    scores_by_role: Dict[int, List[float]] = {}
    all_scores: List[float] = []
    for rid, score in score_rows:
        if rid is None or score is None:
            continue
        scores_by_role.setdefault(rid, []).append(float(score))
        all_scores.append(float(score))

    # ── Assemble — one row per role that has made at least one decision ─
    role_names = {
        rid: name
        for rid, name in db.query(Role.id, Role.name).filter(Role.organization_id == org_id).all()
    }
    role_ids = sorted(
        decisions_by_role.keys(),
        key=lambda rid: decisions_by_role[rid]["total"],
        reverse=True,
    )
    roles: List[Dict[str, Any]] = []
    for rid in role_ids:
        roles.append({
            "role_id": rid,
            "role_name": role_names.get(rid) or f"Role #{rid}",
            "decisions": decisions_by_role.get(rid, _empty_decisions_bucket()),
            "workable_stages": stages_by_role.get(rid, {}),
            "advance_conversion": conversion_by_role.get(rid, _empty_conversion_bucket()),
            "score_stats": _score_summary(scores_by_role.get(rid, [])),
        })

    return {
        "window": window,
        "score_basis": "taali",
        "totals": {
            "decisions": totals_decisions,
            "workable_stages": totals_stages,
            "advance_conversion": totals_conversion,
            "score_stats": _score_summary(all_scores),
        },
        "roles": roles,
    }


# ----------------------------------------------------------------------------
# Activity timeseries — backs the Home "Decisions & notifications over time"
# section. Answers "why did my review queue grow from 300 to 500?" with a
# daily curve of the backlog (the pending count shown on the Home tab badge),
# the decisions the agent created each day (by type), and a current callout of
# decisions that bounced back into the queue because a Workable writeback
# failed. Role-filterable; daily granularity over a trailing window.
# ----------------------------------------------------------------------------

# A decision with no ``resolved_at`` is still "in the queue" while in one of
# these states; any other terminal state with no timestamp is treated as
# closed at creation (it only blips the backlog on its creation day).
_OPEN_DECISION_STATUSES = {"pending", "processing", "reverted_for_feedback"}
# resolution_note prefix written by workable_op_runner._requeue_decision when a
# Workable writeback fails and the decision is bounced back to the queue.
_WORKABLE_REQUEUE_NOTE_PREFIX = "Returned to queue"


def _open_at_day_ends(
    intervals: Sequence[Tuple[Optional[datetime], Optional[datetime]]],
    day_ends: Sequence[datetime],
) -> List[int]:
    """Count how many [start, end) intervals are open at each day boundary.

    ``end is None`` means still open (no close yet). An interval is open at a
    day end ``de`` when ``start <= de < end``.
    """
    counts = [0] * len(day_ends)
    for start, end in intervals:
        if start is None:
            continue
        for i, de in enumerate(day_ends):
            if start <= de and (end is None or end > de):
                counts[i] += 1
    return counts


@router.get("/activity-timeseries")
def get_activity_timeseries(
    role_id: Optional[int] = Query(default=None),
    days: int = Query(default=30, ge=1, le=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily decisions + notification-backlog curve, plus a Workable-error callout."""
    org_id = current_user.organization_id
    if not org_id:
        return {
            "window": {"days": days, "from": None, "to": None},
            "series": [],
            "decision_types": [],
            "pending_now": {"decisions": 0, "questions": 0, "total": 0, "by_type": {}},
            "workable_errors": {"total": 0, "by_role": []},
        }

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_starts = [today_start - timedelta(days=days - 1 - i) for i in range(days)]
    day_ends = [ds + timedelta(days=1) for ds in day_starts]
    window_start = day_starts[0]
    window_start_date = window_start.date()

    def _day_index(ts: Optional[datetime]) -> Optional[int]:
        ts = _ensure_utc(ts)
        if ts is None:
            return None
        idx = (ts.date() - window_start_date).days
        return idx if 0 <= idx < days else None

    # ── Decisions (role-scoped): created/resolved timestamps + type/status ──
    dq = db.query(
        AgentDecision.created_at,
        AgentDecision.resolved_at,
        AgentDecision.status,
        AgentDecision.decision_type,
    ).filter(AgentDecision.organization_id == org_id)
    if role_id is not None:
        dq = dq.filter(AgentDecision.role_id == role_id)
    decision_rows = dq.all()

    # ── Agent questions (role-scoped) — the other half of the badge count ──
    nq = db.query(
        AgentNeedsInput.created_at,
        AgentNeedsInput.resolved_at,
        AgentNeedsInput.dismissed_at,
    ).filter(AgentNeedsInput.organization_id == org_id)
    if role_id is not None:
        nq = nq.filter(AgentNeedsInput.role_id == role_id)
    needs_rows = nq.all()

    created_total = [0] * days
    resolved_total = [0] * days
    by_type: List[Dict[str, int]] = [dict() for _ in range(days)]
    type_keys: set[str] = set()
    dec_intervals: List[Tuple[Optional[datetime], Optional[datetime]]] = []

    for created, resolved, status, dtype in decision_rows:
        created = _ensure_utc(created)
        ci = _day_index(created)
        if ci is not None:
            created_total[ci] += 1
            key = str(dtype or "unknown")
            by_type[ci][key] = by_type[ci].get(key, 0) + 1
            type_keys.add(key)
        if resolved is not None:
            ri = _day_index(resolved)
            if ri is not None:
                resolved_total[ri] += 1
        # Backlog interval close time.
        if resolved is not None:
            end = _ensure_utc(resolved)
        elif str(status or "").lower() in _OPEN_DECISION_STATUSES:
            end = None
        else:
            end = created
        dec_intervals.append((created, end))

    needs_intervals: List[Tuple[Optional[datetime], Optional[datetime]]] = []
    for created, resolved, dismissed in needs_rows:
        created = _ensure_utc(created)
        close = resolved or dismissed
        needs_intervals.append((created, _ensure_utc(close) if close is not None else None))

    backlog = _open_at_day_ends(dec_intervals + needs_intervals, day_ends)

    series = [
        {
            "date": day_starts[i].date().isoformat(),
            "created": created_total[i],
            "resolved": resolved_total[i],
            "backlog": backlog[i],
            "by_type": by_type[i],
        }
        for i in range(days)
    ]

    # ── Current pending (reconciles with the Home tab badge) ───────────────
    pending_decisions = (
        db.query(func.count(AgentDecision.id))
        .filter(AgentDecision.organization_id == org_id, pending_filter(now))
    )
    if role_id is not None:
        pending_decisions = pending_decisions.filter(AgentDecision.role_id == role_id)
    pending_decisions_count = int(pending_decisions.scalar() or 0)

    pending_questions = (
        db.query(func.count(AgentNeedsInput.id))
        .filter(AgentNeedsInput.organization_id == org_id, open_needs_input_filter())
    )
    if role_id is not None:
        pending_questions = pending_questions.filter(AgentNeedsInput.role_id == role_id)
    pending_questions_count = int(pending_questions.scalar() or 0)

    # Pending decisions split by type — backs the "Pending now · by type"
    # summary chips in the same Hub section (role-aware via the same filter).
    pending_by_type_q = (
        db.query(AgentDecision.decision_type, func.count(AgentDecision.id))
        .filter(AgentDecision.organization_id == org_id, pending_filter(now))
    )
    if role_id is not None:
        pending_by_type_q = pending_by_type_q.filter(AgentDecision.role_id == role_id)
    pending_by_type = {
        str(dtype or "unknown"): int(count)
        for dtype, count in pending_by_type_q.group_by(AgentDecision.decision_type).all()
    }

    # ── Workable-error callout: decisions bounced back to the queue ────────
    err_q = (
        db.query(AgentDecision.role_id, AgentDecision.resolution_note)
        .filter(
            AgentDecision.organization_id == org_id,
            AgentDecision.status == "pending",
            AgentDecision.resolution_note.ilike(f"{_WORKABLE_REQUEUE_NOTE_PREFIX}%"),
        )
    )
    if role_id is not None:
        err_q = err_q.filter(AgentDecision.role_id == role_id)
    err_rows = err_q.all()
    role_names = {
        rid: name
        for rid, name in db.query(Role.id, Role.name).filter(Role.organization_id == org_id).all()
    }
    err_by_role: Dict[int, Dict[str, Any]] = {}
    for rid, note in err_rows:
        bucket = err_by_role.setdefault(
            rid, {"role_id": rid, "role_name": role_names.get(rid) or f"Role #{rid}", "count": 0, "example": None}
        )
        bucket["count"] += 1
        if not bucket["example"] and note:
            bucket["example"] = str(note)[:200]
    workable_errors = {
        "total": len(err_rows),
        "by_role": sorted(err_by_role.values(), key=lambda b: b["count"], reverse=True),
    }

    return {
        "window": {"days": days, "from": window_start.isoformat(), "to": now.isoformat()},
        "series": series,
        "decision_types": sorted(type_keys),
        "pending_now": {
            "decisions": pending_decisions_count,
            "questions": pending_questions_count,
            "total": pending_decisions_count + pending_questions_count,
            "by_type": pending_by_type,
        },
        "workable_errors": workable_errors,
    }


# ---------------------------------------------------------------------------
# A/B experiment comparison (Phase 2 trial)
# ---------------------------------------------------------------------------

# Minimum completed-per-arm before any ranking is meaningful. Mirrors the
# benchmarks gate; during a pilot we stay below this and never declare a winner.
MIN_AB_SAMPLE = 20


def _rate(numer: int, denom: int) -> Optional[float]:
    if not denom:
        return None
    return round(numer / denom, 3)


def _avg(values: Sequence[float], ndigits: int = 1) -> Optional[float]:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return round(sum(vals) / len(vals), ndigits)


def _ab_arm_metrics(arm, arm_assessments, apps_by_id, *, min_sample: int) -> Dict[str, Any]:
    """All three win-signal families for one experiment arm.

    Rates carry their denominator ``n`` so the UI can show "3/5" not "60%".
    The cohort is random-assignment only (forced recruiter picks are excluded
    upstream) so the comparison is apples-to-apples.
    """
    assigned = list(arm_assessments)
    n_assigned = len(assigned)
    started = [a for a in assigned if getattr(a, "started_at", None) is not None]
    completed = [a for a in assigned if _is_completed(a)]
    invited_not_started = [a for a in assigned if getattr(a, "started_at", None) is None]
    abandoned = [a for a in started if not _is_completed(a)]
    timed_out = [a for a in completed if bool(getattr(a, "completed_due_to_timeout", False))]

    scores = [s for s in (_score_100(a) for a in completed) if s is not None]
    durations = [
        int(a.total_duration_seconds)
        for a in completed
        if isinstance(getattr(a, "total_duration_seconds", None), (int, float))
    ]
    score_sum = _score_summary(scores)
    spread_iqr = (
        round(score_sum["p75"] - score_sum["p25"], 1)
        if score_sum.get("p25") is not None and score_sum.get("p75") is not None
        else None
    )

    apps = [apps_by_id.get(a.application_id) for a in assigned if a.application_id]
    apps = [ap for ap in apps if ap is not None]
    n_with_app = len(apps)

    def _stage(ap) -> str:
        return str(getattr(ap, "pipeline_stage", "") or "").lower()

    def _outcome(ap) -> str:
        return str(getattr(ap, "application_outcome", "") or "").lower()

    advanced = sum(1 for ap in apps if _stage(ap) == "advanced" or _outcome(ap) == "hired")
    hired = sum(1 for ap in apps if _outcome(ap) == "hired")
    rejected = sum(1 for ap in apps if _outcome(ap) == "rejected")
    n_with_outcome = sum(1 for ap in apps if _outcome(ap) in {"hired", "rejected", "withdrawn"})

    tab_switches = [int(getattr(a, "tab_switch_count", 0) or 0) for a in started]
    focus = [
        float(a.browser_focus_ratio)
        for a in started
        if isinstance(getattr(a, "browser_focus_ratio", None), (int, float))
    ]
    ttfp = [
        int(a.time_to_first_prompt_seconds)
        for a in started
        if isinstance(getattr(a, "time_to_first_prompt_seconds", None), (int, float))
    ]

    return {
        "arm_id": int(arm.id),
        "arm_key": arm.arm_key,
        "task_id": int(arm.task_id),
        "task_name": getattr(getattr(arm, "task", None), "name", None),
        "knob_overrides": arm.knob_overrides or None,
        "n_assigned": n_assigned,
        "n_started": len(started),
        "n_completed": len(completed),
        "discrimination": {"score": score_sum, "spread_iqr": spread_iqr},
        "completion": {
            "never_started": len(invited_not_started),
            "never_started_rate": _rate(len(invited_not_started), n_assigned),
            "abandoned": len(abandoned),
            "abandonment_rate": _rate(len(abandoned), n_assigned),
            "timed_out": len(timed_out),
            "timeout_rate": _rate(len(timed_out), len(completed)),
            "time_to_complete_seconds": _score_summary(durations),
        },
        "outcome": {
            "n_with_application": n_with_app,
            "advanced": advanced,
            "advanced_rate": _rate(advanced, n_with_app),
            "hired": hired,
            "hired_rate": _rate(hired, n_with_app),
            "rejected": rejected,
            "rejected_rate": _rate(rejected, n_with_app),
            "n_with_outcome": n_with_outcome,
        },
        "experience": {
            "instructions_dropoff_rate": _rate(len(invited_not_started), n_assigned),
            "avg_tab_switches": _avg(tab_switches, 1),
            "avg_browser_focus_ratio": _avg(focus, 2),
            "avg_time_to_first_prompt_seconds": _avg(ttfp, 1),
        },
        "small_sample": len(completed) < min_sample,
    }


@router.get("/experiments/comparison")
def get_experiment_comparison(
    experiment_id: Optional[int] = Query(default=None),
    role_id: Optional[int] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time (inclusive)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compare A/B experiment arms across the three win-signal families.

    Without ``experiment_id`` returns the org's experiments (optionally filtered
    by ``role_id``) so the UI can populate a selector. With it, returns per-arm
    discrimination / completion+time / downstream-outcome / candidate-experience
    metrics over the random-assignment cohort only. Never declares a winner while
    any arm is below the minimum sample — pilot honesty.
    """
    org_id = current_user.organization_id

    if experiment_id is None:
        q = db.query(AssessmentExperiment).filter(
            AssessmentExperiment.organization_id == org_id
        )
        if role_id is not None:
            q = q.filter(AssessmentExperiment.role_id == role_id)
        exps = (
            q.options(selectinload(AssessmentExperiment.arms))
            .order_by(AssessmentExperiment.id.desc())
            .all()
        )
        return {
            "experiments": [
                {
                    "id": int(e.id),
                    "key": e.key,
                    "name": e.name,
                    "role_id": int(e.role_id),
                    "status": e.status,
                    "experiment_type": e.experiment_type,
                    "arm_count": len(e.arms),
                }
                for e in exps
            ]
        }

    exp = (
        db.query(AssessmentExperiment)
        .options(selectinload(AssessmentExperiment.arms))
        .filter(
            AssessmentExperiment.id == experiment_id,
            AssessmentExperiment.organization_id == org_id,
        )
        .first()
    )
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")

    dt_from = _parse_filter_datetime(date_from)
    dt_to = _parse_filter_datetime(date_to, end_of_day=True)

    aq = db.query(Assessment).filter(
        Assessment.experiment_id == exp.id,
        Assessment.organization_id == org_id,
        Assessment.assignment_method == "random",
        Assessment.is_voided.is_(False),
    )
    if dt_from is not None:
        aq = aq.filter(Assessment.created_at >= dt_from)
    if dt_to is not None:
        aq = aq.filter(Assessment.created_at <= dt_to)
    assessments = aq.all()

    app_ids = {a.application_id for a in assessments if a.application_id}
    apps_by_id: Dict[int, CandidateApplication] = {}
    if app_ids:
        for ap in (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(app_ids))
            .all()
        ):
            apps_by_id[int(ap.id)] = ap

    by_arm: Dict[int, List[Assessment]] = {}
    for a in assessments:
        by_arm.setdefault(int(a.experiment_arm_id or 0), []).append(a)

    arms_out = [
        _ab_arm_metrics(arm, by_arm.get(int(arm.id), []), apps_by_id, min_sample=MIN_AB_SAMPLE)
        for arm in exp.arms
    ]

    any_small = (not arms_out) or any(arm["small_sample"] for arm in arms_out)
    assigned_counts = [arm["n_assigned"] for arm in arms_out]
    cohort_drift = False
    if assigned_counts and max(assigned_counts) > 0:
        mn, mx = min(assigned_counts), max(assigned_counts)
        cohort_drift = (mx - mn) >= max(5, 0.5 * mx)

    return {
        "experiment": {
            "id": int(exp.id),
            "key": exp.key,
            "name": exp.name,
            "role_id": int(exp.role_id),
            "status": exp.status,
            "experiment_type": exp.experiment_type,
        },
        "min_sample_threshold": MIN_AB_SAMPLE,
        "arms": arms_out,
        "winner": None,
        "cohort_drift": cohort_drift,
        "guidance": (
            "Pilot — sample too small to call a winner."
            if any_small
            else "All arms meet the minimum sample; compare with care (no winner is auto-declared)."
        ),
    }


# ---------------------------------------------------------------------------
# Decision trend — backs the Outcomes "override rate over time" bar chart and
# the Teaching "agreement trend" bars on the standalone Analytics page. For the
# last ~6 calendar months, computes per month the override rate (overridden /
# resolved) and its complement, the agreement rate (= 100 − override). Counts
# only RESOLVED agent decisions (pending/sent-back rows aren't a verdict yet),
# org-scoped and optionally role-scoped, so it mirrors the human-review KPIs in
# reporting-summary. No synthetic series — a month with no resolved decisions
# reports 0 across the board and the UI renders it as an empty bar.
# ---------------------------------------------------------------------------


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, (idx % 12) + 1


@router.get("/decision-trend")
def get_decision_trend(
    role_id: Optional[int] = Query(default=None),
    months: int = Query(default=6, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Monthly override / agreement rate over resolved agent decisions.

    Returns one entry per calendar month for the trailing ``months`` window
    (oldest first), each carrying the resolved-decision count, the override
    rate (overridden / resolved) and the agreement rate (100 − override). The
    decision engine's verdicts are the source — never fabricated.
    """
    org_id = current_user.organization_id
    now = datetime.now(timezone.utc)
    # Build the ordered list of (year, month) buckets ending with the current
    # month, then a start boundary so the DB scan is bounded.
    cur_year, cur_month = now.year, now.month
    buckets: List[Tuple[int, int]] = [
        _add_months(cur_year, cur_month, -(months - 1 - i)) for i in range(months)
    ]
    empty = {
        "months": [
            {
                "month": f"{y:04d}-{m:02d}",
                "override_rate_pct": 0.0,
                "agreement_rate_pct": 0.0,
                "decisions": 0,
            }
            for (y, m) in buckets
        ]
    }
    if not org_id:
        return empty

    start_year, start_month = buckets[0]
    window_start = datetime(start_year, start_month, 1, tzinfo=timezone.utc)

    q = db.query(AgentDecision.created_at, AgentDecision.status).filter(
        AgentDecision.organization_id == org_id,
        AgentDecision.created_at >= window_start,
    )
    if role_id is not None:
        q = q.filter(AgentDecision.role_id == role_id)

    resolved_by_month: Dict[str, int] = {}
    overridden_by_month: Dict[str, int] = {}
    for created, status in q.all():
        created = _ensure_utc(created)
        if created is None:
            continue
        status_l = str(status or "").lower()
        if status_l in _NON_RESOLVED_DECISION_STATUSES:
            continue
        key = _month_key(created)
        resolved_by_month[key] = resolved_by_month.get(key, 0) + 1
        if status_l == "overridden":
            overridden_by_month[key] = overridden_by_month.get(key, 0) + 1

    out_months: List[Dict[str, Any]] = []
    for (y, m) in buckets:
        key = f"{y:04d}-{m:02d}"
        resolved = resolved_by_month.get(key, 0)
        overridden = overridden_by_month.get(key, 0)
        override_pct = round((overridden / resolved) * 100.0, 1) if resolved else 0.0
        agreement_pct = round(100.0 - override_pct, 1) if resolved else 0.0
        out_months.append({
            "month": key,
            "override_rate_pct": override_pct,
            "agreement_rate_pct": agreement_pct,
            "decisions": resolved,
        })

    return {"months": out_months}


# ---------------------------------------------------------------------------
# Threshold history — backs the Teaching "how the agent calibrated" timeline on
# the standalone Analytics page. The role-fit advance/reject boundary is a real
# persisted artefact: ``ThresholdCalibration`` rows (active / superseded /
# discarded) carry the learned cut, when it activated, and the learner metric.
# When such rows exist we return the genuine change history (newest first);
# when they DON'T we return a single entry for the role's current resolved
# threshold and set ``has_history=false`` — we never invent past changes.
# ---------------------------------------------------------------------------


@router.get("/threshold-history")
def get_threshold_history(
    role_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The agent's score-threshold change history for one role.

    Real source: the ``threshold_calibrations`` table (per-role, then the
    org-wide pool). Each activated/superseded row is a genuine boundary change.
    Falls back to the single current effective threshold with
    ``has_history=false`` when no calibration has ever been activated — no
    fabricated history.
    """
    org_id = current_user.organization_id
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .first()
        if org_id
        else None
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    # Current effective boundary (manual pin → learned calibration → dynamic
    # heuristic). Read defensively so a resolver hiccup never 500s the page.
    current_threshold: Optional[float] = None
    try:
        from ...services.auto_threshold_service import resolve_role_fit_threshold

        resolved = resolve_role_fit_threshold(db, role=role)
        current_threshold = round(float(resolved), 1) if resolved is not None else None
    except Exception:  # pragma: no cover — never break the page on resolution
        current_threshold = (
            float(role.score_threshold) if role.score_threshold is not None else None
        )

    entries: List[Dict[str, Any]] = []
    try:
        from ...models.threshold_calibration import (
            STATUS_ACTIVE,
            STATUS_SUPERSEDED,
            ThresholdCalibration,
        )

        rows = (
            db.query(ThresholdCalibration)
            .filter(
                ThresholdCalibration.organization_id == org_id,
                ThresholdCalibration.status.in_([STATUS_ACTIVE, STATUS_SUPERSEDED]),
                or_(
                    ThresholdCalibration.role_id == role_id,
                    ThresholdCalibration.role_id.is_(None),
                ),
            )
            .order_by(
                desc(
                    func.coalesce(
                        ThresholdCalibration.activated_at,
                        ThresholdCalibration.created_at,
                    )
                )
            )
            .all()
        )
        for row in rows:
            at = row.activated_at or row.created_at
            scope_note = "role-specific" if row.role_id is not None else "org-wide pool"
            metric_bits = []
            if row.metric_name:
                metric_bits.append(str(row.metric_name).replace("_", " "))
            if row.n_positive or row.n_negative:
                metric_bits.append(
                    f"{int(row.n_positive or 0)}+/{int(row.n_negative or 0)}− labels"
                )
            note = scope_note
            if metric_bits:
                note = f"{scope_note} · " + " · ".join(metric_bits)
            entries.append({
                "at": at.isoformat() if at is not None else None,
                "threshold": round(float(row.learned_threshold), 1),
                "status": str(row.status),
                "scope": str(row.scope),
                "note": note,
            })
    except Exception:  # pragma: no cover — calibration is optional infra
        entries = []

    has_history = len(entries) > 0
    if not has_history:
        # No persisted calibration history — surface only the current boundary
        # as a single entry, flagged so the UI never implies past changes.
        entries = [{
            "at": None,
            "threshold": current_threshold,
            "status": "current",
            "scope": "role" if role.score_threshold is not None else "auto",
            "note": (
                "Current threshold — no calibration history recorded yet."
            ),
        }]

    return {
        "role_id": int(role_id),
        "role_name": role.name,
        "current_threshold": current_threshold,
        "has_history": has_history,
        "entries": entries,
    }
