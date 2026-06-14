"""Dynamic Stage-1 pre-screen GATE threshold (data-driven, shadow-first).

The Stage-1 gate filters candidates whose pre-screen score is below a cutoff,
skipping the expensive full score — an effective pre-assessment reject. That
cutoff used to be a static per-service env knob (``PRE_SCREEN_THRESHOLD``), which
drifted (50 on the API, 30 on the workers). This derives it from data instead:
the HIGHEST pre-screen cutoff at which the FALSE-REJECT rate — the share of
candidates who WOULD clear the downstream send bar yet sit below the cutoff —
stays under a small budget (default 1%).

Signal (org-wide, absolute — mirrors the org-wide send bar philosophy in
``auto_threshold_service``; one value per org, applied to every role):
  * Survivors — real ``(raw pre_screen llm_score, full role_fit_score)`` for
    candidates the gate passed and that got full-scored. Free, correct engine.
  * Shadow rejects — ``(pre_screen_score, full_cv_match_score)`` from
    ``prescreen_calibration_samples`` (full-scored in shadow so the sub-gate
    region is observed). See ``services.prescreen_calibration``.

A pair is a POSITIVE ("would clear") iff its full score >= the org send bar
(``compute_role_fit_send_threshold``). The chosen cutoff maximises filtering
subject to ``false_reject_rate <= GATE_FR_BUDGET``, clamped to
``[GATE_FLOOR, GATE_CEILING]`` and gated behind a minimum sample count (too
little data → ``insufficient_data`` so the caller keeps the static floor).

Computed from the RAW, pre-penalty ``llm_score_100`` over the UNFILTERED
population so the cut can never censor its own input (a percentile that learns
from post-gate scores climbs forever). The chosen cut is a CHEAP floor (band
[20, 45]) deliberately well below the send bar — its only job is to skip wasted
full scoring on obvious misfits, not to make the send/reject decision.

SHADOW-ONLY today: the orchestrator stamps the computed value next to the static
one for measurement; enforcement is behind ``PRE_SCREEN_DYNAMIC_GATE_ENFORCE``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.prescreen_calibration_sample import PrescreenCalibrationSample
from ..models.role import Role

logger = logging.getLogger("taali.prescreen_gate_calibration")

# Max tolerated false-reject rate among would-clear candidates. The gate's whole
# point is that filtering here is irreversible (no full score, an effective
# reject), so the budget is deliberately tiny.
GATE_FR_BUDGET = 0.01
# The gate is a cheap floor, NOT the send/reject bar — clamp it to a low band
# well under the send bar so it can only catch obvious misfits.
GATE_FLOOR = 20
GATE_CEILING = 45
# Don't trust a learned cut without a usable sample.
_MIN_PAIRS = 30
_MIN_POSITIVES = 8

# Live-read cache: the gate consults this per scored candidate, so recompute at
# most every few hours per org rather than on every job.
_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[int, tuple[float, "GateThresholdRecommendation"]] = {}


@dataclass
class GateThresholdRecommendation:
    value: int
    source: str  # "calibrated" | "insufficient_data"
    rationale: str
    sample_size: int
    n_positive: int
    fr_rate: float       # realized false-reject rate at the chosen cut
    filtered_frac: float  # share of all sampled candidates the cut would filter

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": int(self.value),
            "source": self.source,
            "rationale": self.rationale,
            "sample_size": int(self.sample_size),
            "n_positive": int(self.n_positive),
            "fr_rate": round(float(self.fr_rate), 4),
            "filtered_frac": round(float(self.filtered_frac), 4),
        }


def _evidence_pre_score(evidence: Any) -> float | None:
    """The RAW, pre-penalty pre-screen score (``llm_score_100``)."""
    if isinstance(evidence, dict):
        v = evidence.get("llm_score_100")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _org_pairs(db: Session, *, organization_id: int) -> list[tuple[float, float]]:
    """``(raw_pre_screen_score, full_score)`` across the org: survivors (real,
    in prod) + shadow-scored rejects."""
    pairs: list[tuple[float, float]] = []
    # Survivors — full-scored in prod (the gate let them through). cv_match_score
    # is the role-fit (holistic ``overall``); pre_screen_filtered rows have a
    # NULL cv_match_score and are naturally excluded.
    survivors = (
        db.query(
            CandidateApplication.pre_screen_evidence,
            CandidateApplication.pre_screen_score_100,
            CandidateApplication.cv_match_score,
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.isnot(None),
        )
        .all()
    )
    for evidence, col_score, full in survivors:
        pre = _evidence_pre_score(evidence)
        if pre is None and col_score is not None:
            pre = float(col_score)
        if pre is not None and full is not None:
            pairs.append((pre, float(full)))
    # Shadow rejects — full-scored in shadow (the gate filtered them).
    rejects = (
        db.query(
            PrescreenCalibrationSample.pre_screen_score,
            PrescreenCalibrationSample.full_cv_match_score,
        )
        .filter(
            PrescreenCalibrationSample.organization_id == organization_id,
            PrescreenCalibrationSample.scoring_status == "ok",
            PrescreenCalibrationSample.full_cv_match_score.isnot(None),
            PrescreenCalibrationSample.pre_screen_score.isnot(None),
        )
        .all()
    )
    for pre, full in rejects:
        if pre is not None and full is not None:
            pairs.append((float(pre), float(full)))
    return pairs


def _max_cut_within_budget(
    pairs: list[tuple[float, float]], send_bar: float
) -> tuple[int, float, float, int] | None:
    """Highest integer cut C in ``[GATE_FLOOR, GATE_CEILING]`` whose false-reject
    rate <= ``GATE_FR_BUDGET``.

    Returns ``(cut, fr_rate, filtered_frac, n_positive)`` or ``None`` when there
    are too few would-clear positives to estimate the rate.
    """
    positives = [pre for pre, full in pairs if full >= send_bar]
    P = len(positives)
    if P < _MIN_POSITIVES:
        return None
    n = len(pairs)
    best: tuple[int, float, float, int] | None = None
    # false-reject count is monotonic non-decreasing in C, so the C's within
    # budget form a prefix — climb and keep the highest, break once over.
    for C in range(GATE_FLOOR, GATE_CEILING + 1):
        fr = sum(1 for pre in positives if pre < C) / P
        if fr <= GATE_FR_BUDGET:
            filtered = (sum(1 for pre, _ in pairs if pre < C) / n) if n else 0.0
            best = (C, fr, filtered, P)
        else:
            break
    return best


def compute_gate_threshold(db: Session, *, role: Role) -> GateThresholdRecommendation:
    """Org-wide, data-driven Stage-1 gate cutoff (shadow). ``role`` is used only
    for its ``organization_id`` + to resolve the org send bar — the value is
    identical for every role in the org by design."""
    from .auto_threshold_service import compute_role_fit_send_threshold

    org_id = int(role.organization_id)
    pairs = _org_pairs(db, organization_id=org_id)
    if len(pairs) < _MIN_PAIRS:
        return GateThresholdRecommendation(
            value=GATE_FLOOR,
            source="insufficient_data",
            rationale=(
                f"Only {len(pairs)} (pre-screen, full-score) pairs in the org — "
                f"below the {_MIN_PAIRS} floor; holding at the gate floor {GATE_FLOOR}."
            ),
            sample_size=len(pairs), n_positive=0, fr_rate=0.0, filtered_frac=0.0,
        )
    send_bar = float(compute_role_fit_send_threshold(db, role=role).value)
    res = _max_cut_within_budget(pairs, send_bar)
    if res is None:
        return GateThresholdRecommendation(
            value=GATE_FLOOR,
            source="insufficient_data",
            rationale=(
                f"Fewer than {_MIN_POSITIVES} would-clear candidates (send bar "
                f"{send_bar:.0f}) in {len(pairs)} pairs; holding at the gate floor {GATE_FLOOR}."
            ),
            sample_size=len(pairs), n_positive=0, fr_rate=0.0, filtered_frac=0.0,
        )
    cut, fr, filtered, P = res
    return GateThresholdRecommendation(
        value=int(cut),
        source="calibrated",
        rationale=(
            f"Highest pre-screen cut keeping the false-reject rate <= {GATE_FR_BUDGET:.0%}: "
            f"cut {cut} filters {filtered:.0%} of {len(pairs)} candidates while only {fr:.1%} "
            f"of the {P} who'd clear the send bar ({send_bar:.0f}) fall below it."
        ),
        sample_size=len(pairs), n_positive=P, fr_rate=fr, filtered_frac=filtered,
    )


def compute_gate_threshold_cached(db: Session, *, role: Role) -> GateThresholdRecommendation:
    """``compute_gate_threshold`` with a per-org TTL cache so the gate can
    consult it on every scored candidate cheaply. Never raises — on any error
    the caller treats the result as unavailable and keeps the static threshold."""
    org_id = int(role.organization_id)
    now = time.monotonic()
    hit = _cache.get(org_id)
    if hit is not None and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    rec = compute_gate_threshold(db, role=role)
    _cache[org_id] = (now, rec)
    return rec
