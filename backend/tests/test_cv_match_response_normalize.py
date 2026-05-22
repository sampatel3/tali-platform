"""Response-time normalize for ``app.cv_match_score`` doesn't inflate.

A weak v3 candidate (1 of 21 requirements met) lands with
``role_fit_score = 0.4 × cv_fit + 0.6 × requirements_match`` somewhere
in the (0, 10] range — a real, single-digit 0-100 score. The previous
heuristic in role_support's response normalizer turned a 9.6 into 96,
making weak-fit candidates look like top scorers in the gauge while
the requirements panel correctly showed "1 of 21 evidenced". This
test pins the fixed behavior.
"""

from __future__ import annotations

from app.domains.assessments_runtime.role_support import (
    _normalize_cv_match_score_for_response,
    _normalize_score_100_for_response,
)
from app.services.taali_scoring import normalize_score_100


def test_taali_normalize_does_not_inflate_single_digit_scores():
    """A stored 9.6 stays 9.6 — not 96."""
    assert normalize_score_100(9.6) == 9.6
    assert normalize_score_100(5) == 5.0
    assert normalize_score_100(8.27) == 8.3


def test_taali_normalize_does_not_inflate_sub_one_scores():
    """A real aggregate role_fit of 0.4 stays 0.4 — not 40.

    ``role_fit = 0.4·cv_fit + 0.6·requirements_match`` (both 0-100)
    can legitimately produce values in (0, 1] for truly weak
    candidates. The old ``<=1.0 → ×100`` auto-scale would have hidden
    a near-zero fit as a moderate one.
    """
    assert normalize_score_100(0.4) == 0.4
    assert normalize_score_100(0.85) == 0.8  # banker's rounding
    assert normalize_score_100(1.0) == 1.0


def test_taali_normalize_clamps_and_rejects():
    assert normalize_score_100(120) == 100.0
    assert normalize_score_100(-1) is None
    assert normalize_score_100(None) is None
    assert normalize_score_100("nope") is None


def test_cv_match_response_normalize_no_score_scale_does_not_inflate():
    """v3 ``cv_match_details`` from the runner ships without
    ``score_scale``. Make sure a weak 0-100 score isn't 10×'d."""
    assert _normalize_cv_match_score_for_response(9.6, {}) == 9.6
    assert _normalize_cv_match_score_for_response(9.6, None) == 9.6
    assert _normalize_cv_match_score_for_response(72.3, {}) == 72.3


def test_cv_match_response_normalize_respects_explicit_0_10_scale():
    """The one remaining auto-scale branch — when callers explicitly
    tag ``score_scale: "0-10"`` we still rescale."""
    assert _normalize_cv_match_score_for_response(7.5, {"score_scale": "0-10"}) == 75.0


def test_cv_match_response_normalize_passes_through_100_scale():
    assert _normalize_cv_match_score_for_response(9.6, {"score_scale": "0-100"}) == 9.6


def test_score_100_response_normalize_no_inflation():
    """The generic helper that feeds the ``*_cache_100`` columns."""
    assert _normalize_score_100_for_response(9.6) == 9.6
    assert _normalize_score_100_for_response(7) == 7.0
    assert _normalize_score_100_for_response(0.4) == 0.4
    assert _normalize_score_100_for_response(150) == 100.0
