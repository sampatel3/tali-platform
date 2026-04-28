"""Population Stability Index (PSI) drift detection (RALPH 4.5).

PSI = Σ_i (curr_pct_i − prev_pct_i) * ln(curr_pct_i / prev_pct_i)

Classical heuristic interpretation:
- PSI < 0.10 — no significant drift
- 0.10 ≤ PSI < 0.25 — moderate drift, investigate
- PSI ≥ 0.25 — major drift, alert

The job runs nightly. For each role family, it bins the cv_match
score distribution over the current 30-day window and compares
against the prior 30-day window. PSI > ``ALERT_THRESHOLD`` triggers
an alert (logged at WARNING; alerting infra greps for the prefix).

Pure-Python; the math is small enough that numpy is unwarranted.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

logger = logging.getLogger("taali.cv_match.fairness.drift")

ALERT_THRESHOLD = 0.25
INVESTIGATE_THRESHOLD = 0.10
DEFAULT_BIN_EDGES = (0, 25, 50, 75, 90, 100)
_EPS = 1e-9  # avoid log(0); small relative to typical bin counts


@dataclass
class PsiReport:
    role_family: str
    psi: float
    severity: str  # "ok" | "investigate" | "alert"
    n_current: int
    n_previous: int


def _bucket(values: Iterable[float], edges: Sequence[float]) -> list[float]:
    """Histogram counts for ``values`` against ``edges``. Returns a
    list of length len(edges) - 1. The last bin is right-inclusive
    so 100 falls into the topmost bucket."""
    n_bins = len(edges) - 1
    counts = [0] * n_bins
    for v in values:
        # Find the bin index. Right-inclusive on the last bin.
        for i in range(n_bins):
            lo, hi = edges[i], edges[i + 1]
            if i == n_bins - 1:
                if lo <= v <= hi:
                    counts[i] += 1
                    break
            else:
                if lo <= v < hi:
                    counts[i] += 1
                    break
    return [float(c) for c in counts]


def population_stability_index(
    current: Sequence[float],
    previous: Sequence[float],
    *,
    edges: Sequence[float] = DEFAULT_BIN_EDGES,
) -> float:
    """Compute PSI for two score samples.

    Returns 0.0 when either sample is empty (vacuously stable).
    """
    if not current or not previous:
        return 0.0
    n_curr = len(current)
    n_prev = len(previous)
    curr_counts = _bucket(current, edges)
    prev_counts = _bucket(previous, edges)
    psi = 0.0
    for c, p in zip(curr_counts, prev_counts):
        cp = (c / n_curr) if n_curr else 0.0
        pp = (p / n_prev) if n_prev else 0.0
        cp = max(cp, _EPS)
        pp = max(pp, _EPS)
        psi += (cp - pp) * math.log(cp / pp)
    return psi


def _severity(psi: float) -> str:
    if psi >= ALERT_THRESHOLD:
        return "alert"
    if psi >= INVESTIGATE_THRESHOLD:
        return "investigate"
    return "ok"


def _scores_by_role_family_in_window(
    db_session,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, list[float]]:
    """Pull (role_family, role_fit_score) rows from cv_score_jobs / events.

    Role-family attribution: until role-family tagging exists in the DB
    schema, the function reads from ``application.role.title`` slugified
    (matching the calibrator extractor's default mapper). When the DB
    is unavailable, returns ``{}`` cleanly.
    """
    try:
        from ..models.candidate_application import CandidateApplication
        from ..models.role import Role
    except Exception as exc:
        logger.debug("Drift extractor: DB unavailable: %s", exc)
        return {}

    from .impact_ratio import _is_advanced  # noqa — avoid circular import
    from ..calibrators.extractor import _default_role_family_mapper

    by_family: dict[str, list[float]] = defaultdict(list)
    rows = (
        db_session.query(CandidateApplication, Role)
        .outerjoin(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.cv_match_scored_at >= start,
            CandidateApplication.cv_match_scored_at < end,
        )
        .all()
    )
    for app, role in rows:
        details = getattr(app, "cv_match_details", {}) or {}
        score = details.get("role_fit_score")
        if score is None:
            continue
        family = _default_role_family_mapper(getattr(role, "title", None))
        by_family[family].append(float(score))
    return by_family


def run_drift_check(
    *,
    window_days: int = 30,
    now: datetime | None = None,
) -> list[PsiReport]:
    """Run PSI for current vs prior ``window_days`` window per role family.

    Empty list when the DB is unavailable or no data is present in
    either window.
    """
    try:
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Drift check skipped (no DB): %s", exc)
        return []

    now = now or datetime.now(timezone.utc)
    curr_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=2 * window_days)

    session = SessionLocal()
    try:
        curr = _scores_by_role_family_in_window(
            session, start=curr_start, end=now
        )
        prev = _scores_by_role_family_in_window(
            session, start=prev_start, end=curr_start
        )
        reports: list[PsiReport] = []
        all_families = set(curr) | set(prev)
        for family in sorted(all_families):
            c = curr.get(family, [])
            p = prev.get(family, [])
            psi = population_stability_index(c, p)
            severity = _severity(psi)
            reports.append(
                PsiReport(
                    role_family=family,
                    psi=psi,
                    severity=severity,
                    n_current=len(c),
                    n_previous=len(p),
                )
            )
            if severity == "alert":
                logger.warning(
                    "PSI ALERT role_family=%s psi=%.4f n_curr=%d n_prev=%d",
                    family,
                    psi,
                    len(c),
                    len(p),
                )
        return reports
    finally:
        session.close()


__all__ = [
    "ALERT_THRESHOLD",
    "INVESTIGATE_THRESHOLD",
    "PsiReport",
    "population_stability_index",
    "run_drift_check",
]
