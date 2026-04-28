"""Tests for pairwise tie-break (RALPH 3.6) and borderline detection
(RALPH 3.7 / 3.8).

Stubs the Anthropic client so the BT logic is exercised without
network. Verifies:
- Position-bias mitigation: a flipped pair returns "TIE".
- BT MLE: a clearly-stronger candidate beats two anchors and
  produces a scaled_score above their median.
- self_consistency: early-stops when stddev stabilises and the band
  reflects sample variance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching.borderline import (
    SelfConsistencyResult,
    is_borderline,
    self_consistency,
)
from app.cv_matching.pairwise import (
    AnchorCandidate,
    pairwise_score,
    _consistent_outcome,
    _mm_bradley_terry,
    _scale_thetas_to_score,
)


# --------------------------------------------------------------------------- #
# Stub Anthropic                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class _Block:
    text: str


@dataclass
class _Resp:
    text: str

    @property
    def content(self):
        return [_Block(text=self.text)]


@dataclass
class _Msgs:
    """Returns a sequence of canned winners (per call). Each call gets
    one element of ``responses``; when exhausted, repeats the last.
    """

    responses: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        winner = self.responses[min(idx, len(self.responses) - 1)]
        return _Resp(
            text=json.dumps({"winner": winner, "reasoning": "stub"})
        )


@dataclass
class _Client:
    messages: _Msgs


# --------------------------------------------------------------------------- #
# is_borderline + consistent_outcome                                           #
# --------------------------------------------------------------------------- #


def test_is_borderline_default_window():
    assert is_borderline(50)
    assert is_borderline(40)
    assert is_borderline(75)
    assert not is_borderline(39.9)
    assert not is_borderline(75.01)


def test_consistent_outcome_position_swap_consistent_keeps_winner():
    """Both directions agreed A is stronger → real A win."""
    # forward: A vs B → A wins
    # swapped: B vs A → "B" wins (i.e. position-B → original A wins)
    assert _consistent_outcome("A", "B") == "A"


def test_consistent_outcome_position_swap_disagreement_is_tie():
    # forward: A wins. swapped: A wins (position-A is original B → B won) → flip
    assert _consistent_outcome("A", "A") == "TIE"
    # forward TIE → TIE regardless of swapped
    assert _consistent_outcome("TIE", "A") == "TIE"


# --------------------------------------------------------------------------- #
# Bradley-Terry MM                                                             #
# --------------------------------------------------------------------------- #


def test_bt_mle_recovers_clear_winner():
    """If candidate beats both anchors twice, the candidate's θ should
    rank highest after MM convergence."""
    items = ["candidate", "anchor_low", "anchor_high"]
    pair_counts: dict[tuple[str, str], int] = {
        ("candidate", "anchor_low"): 2,
        ("candidate", "anchor_high"): 2,
    }
    wins: dict[tuple[str, str], int] = {
        ("candidate", "anchor_low"): 2,
        ("candidate", "anchor_high"): 2,
    }
    thetas = _mm_bradley_terry(items, pair_counts, wins)
    assert thetas["candidate"] > thetas["anchor_low"]
    assert thetas["candidate"] > thetas["anchor_high"]


def test_scale_thetas_returns_anchor_known_scores_at_anchor_thetas():
    """When θ equals an anchor's θ, the scale returns that anchor's
    known score (within a numerical tolerance)."""
    anchors = [
        AnchorCandidate(label="lo", cv_text="lo", known_score=30.0),
        AnchorCandidate(label="hi", cv_text="hi", known_score=90.0),
    ]
    thetas = {"lo": -1.0, "hi": 1.0, "candidate": 0.0}
    scale = _scale_thetas_to_score(thetas, anchors)
    assert abs(scale(-1.0) - 30.0) < 1e-9
    assert abs(scale(1.0) - 90.0) < 1e-9
    assert 30.0 < scale(0.0) < 90.0  # midpoint between anchors


# --------------------------------------------------------------------------- #
# pairwise_score round-trip                                                    #
# --------------------------------------------------------------------------- #


def test_pairwise_score_consistent_wins_lift_the_score():
    """Candidate consistently beats both anchors → scaled_score above
    the anchor band's mean."""
    anchors = [
        AnchorCandidate(label="lo", cv_text="weak CV", known_score=30.0),
        AnchorCandidate(label="hi", cv_text="strong CV", known_score=70.0),
    ]
    candidate = AnchorCandidate(
        label="cand", cv_text="exceptional CV", known_score=55.0
    )
    # Pair sequence (candidate-vs-lo): forward=A, swapped=B → consistent win for A (candidate).
    # Pair sequence (candidate-vs-hi): forward=A, swapped=B → consistent win for A.
    client = _Client(messages=_Msgs(responses=["A", "B", "A", "B"]))
    res = pairwise_score(
        jd_text="JD",
        requirements=[],
        candidate=candidate,
        anchors=anchors,
        client=client,
    )
    assert res.item_label == "cand"
    # Won 2/2 vs anchors → scaled_score should be above the high anchor.
    assert res.scaled_score >= 70.0


def test_pairwise_score_no_anchors_falls_back_to_known_score():
    candidate = AnchorCandidate(
        label="cand", cv_text="exceptional CV", known_score=58.0
    )
    res = pairwise_score(
        jd_text="JD",
        requirements=[],
        candidate=candidate,
        anchors=[],
        client=_Client(messages=_Msgs(responses=[])),
    )
    assert res.scaled_score == 58.0


# --------------------------------------------------------------------------- #
# self_consistency                                                             #
# --------------------------------------------------------------------------- #


def test_self_consistency_computes_mean_and_std():
    samples_iter = iter([72, 70, 74, 73, 71])
    res = self_consistency(lambda: next(samples_iter), max_samples=5)
    assert len(res.samples) <= 5
    assert 70 < res.mean < 75
    assert res.std >= 0


def test_self_consistency_early_stops_when_stddev_stable():
    # All samples identical → stddev hits 0 quickly and stays.
    res = self_consistency(lambda: 70.0, max_samples=5)
    assert len(res.samples) <= 4  # early stops by sample 3 or 4
    assert res.std == 0.0
    assert res.early_stopped


def test_self_consistency_returns_empty_on_zero_max_samples():
    res = self_consistency(lambda: 1.0, max_samples=0)
    assert res.samples == []
    assert res.mean == 0.0
