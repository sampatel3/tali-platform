"""P2 core analytics: native pipeline funnel + time-to-fill.

Read-only aggregates over the ATS's *own* data (the fixed Tali funnel stages and
the ATS-synced hired outcome) — distinct from the Workable-stage conversion in
``analytics_routes`` which mirrors the external pipeline. Pure functions so the
maths is unit-testable without HTTP.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication

# The canonical Tali funnel stages, in order. Mirrors
# ``pipeline_service.PIPELINE_STAGES`` (the fixed legacy pipeline) with each
# stage's display name + coarse kind. The ATS owns any further pipeline
# customization; Tali reports against this fixed funnel. Each entry is
# ``(slug, name, kind)``.
_CANONICAL_STAGES: tuple[tuple[str, str, str], ...] = (
    ("applied", "Applied", "applied"),
    ("invited", "Invited", "assessment"),
    ("in_assessment", "In assessment", "assessment"),
    ("review", "Review", "review"),
    ("advanced", "Advanced", "interview"),
)


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted, non-empty list."""
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    rank = pct * (n - 1)
    low = int(rank)
    high = min(low + 1, n - 1)
    frac = rank - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac)


def _duration_summary(days: Sequence[float]) -> Dict[str, Any]:
    vals = sorted(float(d) for d in days)
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


def _ordered_stages(db: Session, org_id: int) -> List[Dict[str, Any]]:
    """The canonical Tali funnel stages in order. ``db``/``org_id`` are unused —
    the funnel is a fixed set (the ATS owns any further customization) — but kept
    in the signature so callers don't change."""
    return [
        {"slug": slug, "name": name, "kind": kind}
        for slug, name, kind in _CANONICAL_STAGES
    ]


def pipeline_funnel(
    db: Session, org_id: int, role_id: Optional[int] = None
) -> Dict[str, Any]:
    """Current headcount per configured stage (funnel order), plus outcome mix.

    A snapshot — "where the live pipeline sits now" — not a flow over time. Only
    open (non-deleted) applications are counted per stage; ``outcomes`` reports
    the terminal split (hired / rejected / open) across the whole cohort.
    """
    if not org_id:
        return {"stages": [], "outcomes": {}, "total": 0}

    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org_id,
        CandidateApplication.deleted_at.is_(None),
    )
    if role_id is not None:
        base = base.filter(CandidateApplication.role_id == role_id)

    # One grouped scan for stage counts.
    stage_counts: Dict[str, int] = dict(
        base.with_entities(
            CandidateApplication.pipeline_stage, func.count(CandidateApplication.id)
        ).group_by(CandidateApplication.pipeline_stage).all()
    )
    # One grouped scan for outcome counts.
    outcome_counts: Dict[str, int] = dict(
        base.with_entities(
            CandidateApplication.application_outcome, func.count(CandidateApplication.id)
        ).group_by(CandidateApplication.application_outcome).all()
    )

    ordered = _ordered_stages(db, org_id)
    known = {s["slug"] for s in ordered}
    stages = [
        {**s, "count": int(stage_counts.get(s["slug"], 0))} for s in ordered
    ]
    # Applications sitting on a slug the org no longer lists (e.g. imported /
    # legacy) — surfaced so the funnel total reconciles instead of silently
    # dropping them.
    other = sum(c for slug, c in stage_counts.items() if slug not in known)
    if other:
        stages.append({"slug": "_other", "name": "Other", "kind": None, "count": int(other)})

    total = sum(int(c) for c in stage_counts.values())
    return {
        "stages": stages,
        "outcomes": {k: int(v) for k, v in outcome_counts.items()},
        "total": total,
    }


def time_to_fill(
    db: Session, org_id: int, role_id: Optional[int] = None
) -> Dict[str, Any]:
    """Days from application created to hired, over hired applications.

    Uses the canonical hired signal — ``application_outcome == 'hired'`` with
    ``application_outcome_updated_at`` as the fill timestamp (the moment the
    outcome flipped to hired) — against the application's ``created_at``. Returns
    a duration summary plus a per-role breakdown so a slow-to-fill role stands out.

    Coverage note: the Workable sync and the native pipeline stamp the ``hired``
    outcome, so this is accurate for Workable and standalone orgs. The Bullhorn
    sync currently maps a placed/confirmed candidate to the ``advanced`` STAGE
    only and never writes ``application_outcome='hired'`` (a mere advance must not
    write placement — see bullhorn/stage_map.py), so Bullhorn placements are not
    yet counted here. Wiring Bullhorn placement → hired outcome is a follow-up for
    when the Bullhorn integration is activated (it's dark today, so no org is
    affected). The prior offers-based metric did not capture Bullhorn placements
    either, so this is a pre-existing coverage gap, not a regression.
    """
    if not org_id:
        return {"overall": _duration_summary([]), "by_role": []}

    q = (
        db.query(
            CandidateApplication.application_outcome_updated_at,
            CandidateApplication.created_at,
            CandidateApplication.role_id,
        )
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.application_outcome == "hired",
            CandidateApplication.application_outcome_updated_at.isnot(None),
            CandidateApplication.created_at.isnot(None),
        )
    )
    if role_id is not None:
        q = q.filter(CandidateApplication.role_id == role_id)

    overall_days: List[float] = []
    per_role: Dict[int, List[float]] = {}
    for hired_at, created_at, r_id in q.all():
        days = _days_between(created_at, hired_at)
        if days is None or days < 0:
            continue
        overall_days.append(days)
        per_role.setdefault(r_id, []).append(days)

    by_role = [
        {"role_id": r_id, **_duration_summary(vals)}
        for r_id, vals in sorted(per_role.items())
    ]
    return {"overall": _duration_summary(overall_days), "by_role": by_role}


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _days_between(start: datetime, end: datetime) -> Optional[float]:
    if start is None or end is None:
        return None
    return (_as_utc(end) - _as_utc(start)).total_seconds() / 86400.0
