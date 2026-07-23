"""Compute "do high scorers cluster?" signals for a role.

Given a role with at least N scored applicants, this finds skills,
companies, job titles, and schools that are over-represented in the
top decile of TAALI score relative to the full applicant pool. Each
signal carries a ``lift`` (top_freq ÷ pool_freq) — values > 1 mean
the feature is more common among top scorers than among applicants
generally.

Used by:
- Agent runtime: agent calls a tool that returns these signals so the
  reasoning can cite "high scorers tend to have X; this candidate
  doesn't" when queueing a reject (or "matches the pattern" when
  queueing an advance).
- Future: recruiter UI panel showing "what does the top 10% look like".

The function is read-only and pure — caching/persistence is the
caller's responsibility (see ``role.agent_cohort_signals``).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session, joinedload

from ..candidate_search.application_role_scope import score_expression
from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication


logger = logging.getLogger("taali.cohort_signals")


# Minimum scored applicants required before signals are meaningful. Below
# this we return an explicit "insufficient_data" payload so the agent can
# reason about whether to call the tool at all.
MIN_POOL_SIZE = 5

# What fraction of the pool counts as "top performers". Always at least
# MIN_TOP_SIZE candidates regardless of fraction.
TOP_FRACTION = 0.10
MIN_TOP_SIZE = 5

# Filters applied per-signal before returning:
# - top_freq must be at least this fraction (signal is consistent in top set)
# - lift must be at least this multiple (signal is over-represented vs pool)
MIN_TOP_FREQ = 0.40
MIN_LIFT = 1.5

# Maximum signals returned per category to keep payload small for the
# agent's context window.
MAX_SIGNALS_PER_CATEGORY = 8


def _normalize(s: Any) -> Optional[str]:
    if not isinstance(s, str):
        return None
    out = s.strip().lower()
    return out or None


def _candidate_skills(c: Candidate) -> list[str]:
    skills = c.skills if isinstance(c.skills, list) else []
    return list({n for n in (_normalize(x) for x in skills) if n})


def _entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [e for e in value if isinstance(e, dict)]


def _candidate_companies(c: Candidate) -> list[str]:
    return list(
        {n for n in (_normalize(e.get("company")) for e in _entries(c.experience_entries)) if n}
    )


def _candidate_titles(c: Candidate) -> list[str]:
    return list(
        {n for n in (_normalize(e.get("title")) for e in _entries(c.experience_entries)) if n}
    )


def _candidate_schools(c: Candidate) -> list[str]:
    # CV parser uses ``institution`` for schools.
    return list(
        {n for n in (_normalize(e.get("institution")) for e in _entries(c.education_entries)) if n}
    )


_FEATURE_EXTRACTORS: dict[str, Any] = {
    "skills": _candidate_skills,
    "companies": _candidate_companies,
    "titles": _candidate_titles,
    "schools": _candidate_schools,
}


def _lift_signals(
    *, top_candidates: list[Candidate], rest_candidates: list[Candidate], category: str
) -> list[dict[str, Any]]:
    """Compute lift = top_freq / rest_freq.

    Comparing top against the *rest* (pool minus top) rather than the full
    pool makes "exclusive to top scorers" a meaningful infinity: if a
    feature appears in top but never in rest, lift is unbounded — that's
    the strongest possible signal of clustering.
    """
    extractor = _FEATURE_EXTRACTORS[category]
    top_size = len(top_candidates)
    rest_size = len(rest_candidates)
    if top_size == 0:
        return []

    top_counts: Counter[str] = Counter()
    for c in top_candidates:
        for feat in extractor(c):
            top_counts[feat] += 1

    rest_counts: Counter[str] = Counter()
    for c in rest_candidates:
        for feat in extractor(c):
            rest_counts[feat] += 1

    out: list[dict[str, Any]] = []
    for feat, top_n in top_counts.items():
        top_freq = top_n / top_size
        if top_freq < MIN_TOP_FREQ:
            continue
        rest_n = rest_counts.get(feat, 0)
        rest_freq = (rest_n / rest_size) if rest_size > 0 else 0.0
        exclusive_to_top = rest_n == 0
        if exclusive_to_top:
            # Feature is unique to top scorers — strongest signal.
            lift: float = float("inf")
        else:
            lift = top_freq / rest_freq
        if not exclusive_to_top and lift < MIN_LIFT:
            continue
        out.append(
            {
                "feature": feat,
                "top_freq": round(top_freq, 3),
                "rest_freq": round(rest_freq, 3),
                "lift": (None if lift == float("inf") else round(lift, 2)),
                "exclusive_to_top": exclusive_to_top,
                "top_n": top_n,
                "rest_n": rest_n,
            }
        )

    # Sort: exclusive-to-top first (most striking), then by descending lift.
    def _sort_key(item: dict[str, Any]) -> tuple[int, float]:
        exclusive = 0 if item.get("exclusive_to_top") else 1
        return (exclusive, -(item.get("lift") or 0.0))

    out.sort(key=_sort_key)
    return out[:MAX_SIGNALS_PER_CATEGORY]


def compute_cohort_signals(db: Session, *, role_id: int, organization_id: int) -> dict[str, Any]:
    """Return cohort signals for ``role_id``.

    Output shape::

        {
          "computed_at": "2026-05-07T12:00:00+00:00",
          "pool_size": 47,
          "top_size": 5,
          "top_threshold_score": 78.5,
          "signals": {
            "skills": [{"feature": "kubernetes", "top_freq": 0.8, ...}, ...],
            "companies": [...],
            "titles": [...],
            "schools": [...],
          },
          "insufficient_data": false,
        }

    When ``pool_size < MIN_POOL_SIZE`` returns
    ``{"insufficient_data": true, "pool_size": N, ...}`` with empty signals.
    """
    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
    )
    query = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.organization_id == organization_id,
        )
    )
    query = role_scope.scope_visible_roster(query)
    logical_score = score_expression(role_scope, "taali_score_cache_100")
    apps = query.filter(logical_score.isnot(None)).all()
    evaluations = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in apps],
    )
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=list(apps),
    )
    adapter = role_scope.row_adapter(
        evaluations,
        assessment_truth=assessment_truth,
    )
    pool: list[tuple[float, Candidate]] = []
    for source_application in apps:
        application = (
            adapter(source_application)
            if adapter is not None
            else source_application
        )
        cand = source_application.candidate
        if cand is None:
            continue
        score = application.taali_score_cache_100
        if score is None:
            continue
        pool.append((float(score), cand))

    pool_size = len(pool)
    now = datetime.now(timezone.utc).isoformat()

    if pool_size < MIN_POOL_SIZE:
        return {
            "computed_at": now,
            "pool_size": pool_size,
            "top_size": 0,
            "top_threshold_score": None,
            "signals": {category: [] for category in _FEATURE_EXTRACTORS},
            "insufficient_data": True,
            "min_pool_size": MIN_POOL_SIZE,
        }

    pool.sort(key=lambda t: t[0], reverse=True)
    top_size = max(MIN_TOP_SIZE, int(round(pool_size * TOP_FRACTION)))
    top_size = min(top_size, pool_size)
    top_pairs = pool[:top_size]
    rest_pairs = pool[top_size:]
    top_threshold = top_pairs[-1][0] if top_pairs else None

    top_candidates = [c for _, c in top_pairs]
    rest_candidates = [c for _, c in rest_pairs]

    signals: dict[str, list[dict[str, Any]]] = {}
    for category in _FEATURE_EXTRACTORS:
        signals[category] = _lift_signals(
            top_candidates=top_candidates,
            rest_candidates=rest_candidates,
            category=category,
        )

    return {
        "computed_at": now,
        "pool_size": pool_size,
        "top_size": top_size,
        "top_threshold_score": round(top_threshold, 2) if top_threshold is not None else None,
        "signals": signals,
        "insufficient_data": False,
    }


def render_summary_for_prompt(payload: dict[str, Any], *, max_chars: int = 1200) -> str:
    """Render a tight, prompt-friendly summary of the signals.

    Used by the agent system prompt when cohort signals are stale-but-loaded
    or when the agent calls the get_cohort_signals tool. Skips empty
    categories and truncates at ``max_chars`` to bound prompt size.
    """
    if payload.get("insufficient_data"):
        return (
            f"Cohort signals: insufficient data "
            f"(only {payload.get('pool_size', 0)} scored applicants, "
            f"need {payload.get('min_pool_size', MIN_POOL_SIZE)})."
        )
    lines = [
        f"Top {payload.get('top_size', 0)} of {payload.get('pool_size', 0)} scored "
        f"applicants (threshold TAALI ≥ {payload.get('top_threshold_score', '?')})."
    ]
    signals = payload.get("signals") or {}
    any_signals = False
    for category, items in signals.items():
        if not items:
            continue
        any_signals = True
        rendered = []
        for item in items:
            lift = item.get("lift")
            top_pct = int(item.get("top_freq", 0) * 100)
            tag = (
                f"{item['feature']} (only top, {top_pct}%)"
                if item.get("exclusive_to_top")
                else f"{item['feature']} (lift {lift}×, {top_pct}% of top)"
            )
            rendered.append(tag)
        lines.append(f"- {category}: " + "; ".join(rendered))
    if not any_signals:
        lines.append(
            "- No category showed strong clustering "
            f"(min top_freq {MIN_TOP_FREQ}, min lift {MIN_LIFT}×)."
        )
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 3] + "..."
    return out


__all__ = [
    "compute_cohort_signals",
    "render_summary_for_prompt",
    "MIN_POOL_SIZE",
    "MIN_TOP_FREQ",
    "MIN_LIFT",
]
