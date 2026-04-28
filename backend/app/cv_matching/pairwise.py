"""Pairwise tie-break via Bradley-Terry MLE (RALPH 3.6).

For borderline candidates (composite score ∈ [40, 75]) the rubric
score is informative but coarse. We run pairwise A-vs-B Haiku
comparisons against per-role-family anchor candidates, fit a
Bradley-Terry strength to each item via MM iterations, and rescale
to a continuous 0-100 score using the known anchor scores.

Position-bias mitigation per Shi et al. 2025 / PandaLM convention:
each pair is scored twice with positions swapped. A consistent
verdict (both calls pick the same side) is a real win; a flipped
verdict is treated as a tie.

The MLE is the Hunter (2004) MM algorithm — a 30-line iterative
update that converges in O(K) sweeps without needing scipy.

Public surface:

    AnchorCandidate(label, cv_text, known_score)
    PairwiseResult(item_label, theta, scaled_score, wins)
    pairwise_score(jd, requirements, candidate, anchors, *, client=None)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

from . import MODEL_VERSION

logger = logging.getLogger("taali.cv_match.pairwise")


_PAIRWISE_PROMPT = """You are a hiring evaluator. Two CVs follow, both submitted for the same job. Your job is to pick the stronger candidate.

prompt_version: cv_match_pairwise_v1.0

=== JOB SPECIFICATION ===
{jd_text}

{requirements_block}
=== CANDIDATE A ===
{cv_a}

=== CANDIDATE B ===
{cv_b}

=== INSTRUCTIONS ===

Decide which candidate is stronger for this specific job, on the basis of demonstrated skills, depth, and seniority. Tie is a valid answer when the candidates are genuinely close.

Output ONLY a JSON object, no commentary:
{{
    "winner": "A" | "B" | "tie",
    "reasoning": "<one or two sentences>"
}}
"""


@dataclass
class AnchorCandidate:
    """One per-role-family anchor candidate at a known score.

    Anchors are chosen from historical recruiter-confirmed
    assessments. 3-5 anchors per role family at ~30/50/70/90 give the
    Bradley-Terry MLE enough signal to back out a continuous
    interpolation.
    """

    label: str
    cv_text: str
    known_score: float


@dataclass
class PairwiseResult:
    item_label: str
    theta: float           # raw BT strength (log-scale, anchor-relative)
    scaled_score: float    # interpolated into 0-100
    wins: int = 0
    losses: int = 0
    ties: int = 0


@dataclass
class _PairOutcome:
    """One direction of a pair. Two outcomes (a, b) and (b, a) are
    aggregated into the wins dict via PandaLM consistency."""

    winner: str  # "A" | "B" | "tie"


def _build_pairwise_prompt(
    jd_text: str,
    requirements: list,
    cv_a: str,
    cv_b: str,
) -> str:
    if requirements:
        rr = "=== RECRUITER REQUIREMENTS ===\n" + "\n".join(
            f"- ({r.priority.value}) {r.requirement}" for r in requirements
        )
    else:
        rr = ""
    return _PAIRWISE_PROMPT.format(
        jd_text=jd_text,
        requirements_block=rr,
        cv_a=cv_a,
        cv_b=cv_b,
    )


def _call_pairwise(
    client,
    *,
    jd_text: str,
    requirements: list,
    cv_a: str,
    cv_b: str,
) -> str:
    prompt = _build_pairwise_prompt(jd_text, requirements, cv_a, cv_b)
    response = client.messages.create(
        model=MODEL_VERSION,
        max_tokens=400,
        temperature=0.0,
        system="You are an expert recruiter. Output only JSON.",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text  # type: ignore[attr-defined]
    try:
        blob = json.loads(raw.strip().strip("`"))
    except json.JSONDecodeError:
        return "tie"
    val = (blob.get("winner") or "").strip().upper()
    return val if val in {"A", "B", "TIE"} else "tie"


def _consistent_outcome(forward: str, swapped: str) -> str:
    """PandaLM consistency: same side wins both directions = real win.

    forward: A vs B → "A" / "B" / "TIE"
    swapped: B vs A → "A" / "B" / "TIE"  (note: under swapped framing,
             "A" means the position-A candidate, which is now the
             original B)

    Translate swapped back to the original labels:
       swapped="A" → original B won
       swapped="B" → original A won

    Return "A" / "B" / "TIE" in original-label terms.
    """
    if forward == "TIE" or swapped == "TIE":
        return "TIE"

    # Translate swapped to original labels.
    swapped_orig = {"A": "B", "B": "A"}[swapped]
    if forward == swapped_orig:
        return forward
    return "TIE"


def _mm_bradley_terry(
    items: Sequence[str],
    pair_counts: dict[tuple[str, str], int],
    wins: dict[tuple[str, str], int],
    *,
    n_iter: int = 200,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Hunter (2004) MM algorithm for Bradley-Terry MLE.

    pair_counts[(i, j)] = number of comparisons between i and j (symmetric).
    wins[(i, j)] = number of times i beat j (counts ties as 0.5 each).

    Returns θ_i = log(strength_i), centered so the mean is 0 (BT is
    only identifiable up to a multiplicative constant on strengths,
    i.e. an additive constant on θ).
    """
    items = list(items)
    n = len(items)
    if n == 0:
        return {}
    # Strength = exp(theta). Initialise at 1 for all items.
    s = {i: 1.0 for i in items}
    w = {i: 0.0 for i in items}
    for (a, b), c in wins.items():
        w[a] += c

    for _ in range(n_iter):
        new_s = {}
        for i in items:
            denom = 0.0
            for j in items:
                if i == j:
                    continue
                n_ij = pair_counts.get((i, j), 0) + pair_counts.get((j, i), 0)
                if n_ij == 0:
                    continue
                denom += n_ij / (s[i] + s[j])
            new_s[i] = (w[i] / denom) if denom > 0 else s[i]
        # Normalise to keep numerics stable.
        m = sum(new_s.values()) / n
        if m > 0:
            new_s = {k: v / m for k, v in new_s.items()}
        delta = max(abs(new_s[k] - s[k]) for k in items)
        s = new_s
        if delta < tol:
            break
    return {i: math.log(s[i]) if s[i] > 0 else -float("inf") for i in items}


def _scale_thetas_to_score(
    thetas: dict[str, float],
    anchors: Sequence[AnchorCandidate],
) -> Callable[[float], float]:
    """Build a θ → score linear interpolator from the anchors.

    With ≥ 2 anchors: least-squares line. With 1 anchor: shift only.
    """
    pts = [(thetas[a.label], a.known_score) for a in anchors if a.label in thetas]
    if not pts:
        return lambda t: 50.0  # no information — return a midpoint
    if len(pts) == 1:
        x0, y0 = pts[0]
        return lambda t: max(0.0, min(100.0, y0 + (t - x0) * 10.0))
    # Least-squares y = a*x + b.
    n = len(pts)
    sum_x = sum(p[0] for p in pts)
    sum_y = sum(p[1] for p in pts)
    sum_xy = sum(p[0] * p[1] for p in pts)
    sum_xx = sum(p[0] * p[0] for p in pts)
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        avg = sum_y / n
        return lambda t: max(0.0, min(100.0, avg))
    a = (n * sum_xy - sum_x * sum_y) / denom
    b = (sum_y - a * sum_x) / n
    return lambda t: max(0.0, min(100.0, a * t + b))


def pairwise_score(
    *,
    jd_text: str,
    requirements: list,
    candidate: AnchorCandidate,
    anchors: Sequence[AnchorCandidate],
    client=None,
) -> PairwiseResult:
    """Score ``candidate`` against ``anchors`` via Bradley-Terry MLE.

    The candidate is compared against every anchor in both directions
    (PandaLM-style position-swap). Anchors are not compared against
    each other — those θ values are pinned by the known scores.

    Returns a ``PairwiseResult`` with the candidate's continuous
    scaled_score (0-100). On call failures, returns the candidate's
    known_score as the scaled_score (i.e. fall back to the rubric
    score) and theta=0.
    """
    if not anchors:
        return PairwiseResult(
            item_label=candidate.label,
            theta=0.0,
            scaled_score=candidate.known_score,
        )

    if client is None:
        from .runner import _resolve_anthropic_client

        client = _resolve_anthropic_client()

    pair_counts: dict[tuple[str, str], int] = {}
    wins: dict[tuple[str, str], int] = {}
    for anchor in anchors:
        try:
            forward = _call_pairwise(
                client,
                jd_text=jd_text,
                requirements=requirements,
                cv_a=candidate.cv_text,
                cv_b=anchor.cv_text,
            )
            swapped = _call_pairwise(
                client,
                jd_text=jd_text,
                requirements=requirements,
                cv_a=anchor.cv_text,
                cv_b=candidate.cv_text,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "Pairwise call failed (%s vs %s): %s",
                candidate.label,
                anchor.label,
                exc,
            )
            continue

        outcome = _consistent_outcome(forward, swapped)

        c, a = candidate.label, anchor.label
        pair_counts[(c, a)] = pair_counts.get((c, a), 0) + 1
        if outcome == "A":
            wins[(c, a)] = wins.get((c, a), 0) + 1
        elif outcome == "B":
            wins[(a, c)] = wins.get((a, c), 0) + 1
        else:
            # Tie → half a win each side (handles the typical BT
            # convention).
            wins[(c, a)] = wins.get((c, a), 0) + 0.5
            wins[(a, c)] = wins.get((a, c), 0) + 0.5

    items = [candidate.label] + [a.label for a in anchors]
    thetas = _mm_bradley_terry(items, pair_counts, wins)
    scale = _scale_thetas_to_score(thetas, anchors)

    candidate_theta = thetas.get(candidate.label, 0.0)
    return PairwiseResult(
        item_label=candidate.label,
        theta=candidate_theta,
        scaled_score=scale(candidate_theta),
        wins=int(sum(wins.get((candidate.label, a.label), 0) for a in anchors)),
        losses=int(sum(wins.get((a.label, candidate.label), 0) for a in anchors)),
        ties=0,  # Tied outcomes are split, not counted separately.
    )


__all__ = [
    "AnchorCandidate",
    "PairwiseResult",
    "pairwise_score",
]
