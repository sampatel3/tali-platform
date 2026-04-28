"""Per-segment impact-ratio metrics (RALPH 4.4).

For each protected segment (gender, race, intersectional), compute:

- ``selection_rate``  — fraction of applicants in the segment who
                        receive a "yes" / "strong_yes" recommendation
- ``scoring_rate``    — fraction of applicants in the segment who get
                        a non-failed cv_match score (catches "the
                        pipeline drops segment X more often than Y"
                        even when the threshold-crossing is fine)
- ``impact_ratio``    — segment_selection_rate / reference_selection_rate

The reference segment is the one with the highest selection rate. NYC
LL144's "4/5 rule" requires impact_ratio ≥ 0.80; we add a 5pp safety
margin and use 0.85 as the green threshold (matching the RALPH spec).

The dashboard rolls these up over a rolling 90-day window.

Segment attribution is *not* automatic — the platform doesn't store
self-identified gender/race on candidates. Callers supply a mapping
``application_id -> segment_dict`` (e.g. from a separate diversity-
self-id system) and the math runs on that. When no segment data is
available, the dashboard reports a single "all" row.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping


GREEN_THRESHOLD = 0.85
AMBER_THRESHOLD = 0.80


@dataclass
class SegmentRow:
    segment_key: str
    n_applications: int
    n_scored: int
    n_advanced: int
    selection_rate: float
    scoring_rate: float
    impact_ratio: float | None  # None on the reference segment
    rag: str  # "green" | "amber" | "red"


@dataclass
class ApplicationOutcome:
    """One row from the rolling window."""

    application_id: int
    recommendation: str | None  # "strong_yes" / "yes" / "lean_no" / "no" / None
    scoring_status: str  # "ok" / "failed" / "deferred"


def _is_advanced(recommendation: str | None) -> bool:
    return (recommendation or "").lower() in ("yes", "strong_yes", "advance")


def _rag(impact_ratio: float | None) -> str:
    if impact_ratio is None:
        return "green"  # reference segment is by definition green
    if impact_ratio >= GREEN_THRESHOLD:
        return "green"
    if impact_ratio >= AMBER_THRESHOLD:
        return "amber"
    return "red"


def compute_impact_ratios(
    outcomes: Iterable[ApplicationOutcome],
    segment_for_application: Mapping[int, str],
) -> list[SegmentRow]:
    """Compute per-segment selection/scoring rates and impact ratios.

    ``segment_for_application`` maps ``application_id`` to a segment
    key. Applications without a segment are bucketed under "unknown".
    Returns one ``SegmentRow`` per segment, sorted by segment_key.
    """
    n_total: Counter[str] = Counter()
    n_scored: Counter[str] = Counter()
    n_advanced: Counter[str] = Counter()

    for o in outcomes:
        seg = segment_for_application.get(o.application_id, "unknown")
        n_total[seg] += 1
        if (o.scoring_status or "").lower() == "ok":
            n_scored[seg] += 1
        if _is_advanced(o.recommendation):
            n_advanced[seg] += 1

    if not n_total:
        return []

    # Selection rate per segment.
    selection_rates: dict[str, float] = {}
    for seg in n_total:
        denom = n_total[seg]
        selection_rates[seg] = (n_advanced[seg] / denom) if denom else 0.0

    reference_rate = max(selection_rates.values()) if selection_rates else 0.0
    rows: list[SegmentRow] = []
    for seg in sorted(n_total):
        sel = selection_rates[seg]
        scoring = (n_scored[seg] / n_total[seg]) if n_total[seg] else 0.0
        if reference_rate > 0 and sel < reference_rate:
            ir = sel / reference_rate
        elif reference_rate == 0:
            ir = None
        else:
            ir = None  # this segment IS the reference
        rows.append(
            SegmentRow(
                segment_key=seg,
                n_applications=n_total[seg],
                n_scored=n_scored[seg],
                n_advanced=n_advanced[seg],
                selection_rate=sel,
                scoring_rate=scoring,
                impact_ratio=ir,
                rag=_rag(ir),
            )
        )
    return rows


def compute_intersectional_ratios(
    outcomes: Iterable[ApplicationOutcome],
    segment_dict_for_application: Mapping[int, Mapping[str, str]],
    *,
    axes: tuple[str, ...] = ("gender", "race"),
) -> list[SegmentRow]:
    """Same as ``compute_impact_ratios`` but bucketed on multiple axes.

    The segment_key is a slash-joined combination, e.g. ``female/black``.
    """
    flat: dict[int, str] = {}
    outcomes = list(outcomes)
    for o in outcomes:
        d = segment_dict_for_application.get(o.application_id, {})
        key = "/".join(d.get(a, "unknown") for a in axes)
        flat[o.application_id] = key
    return compute_impact_ratios(outcomes, flat)


__all__ = [
    "ApplicationOutcome",
    "SegmentRow",
    "GREEN_THRESHOLD",
    "AMBER_THRESHOLD",
    "compute_impact_ratios",
    "compute_intersectional_ratios",
]
