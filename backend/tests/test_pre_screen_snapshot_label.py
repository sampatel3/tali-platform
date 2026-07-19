"""Pre-screen snapshots keep Stage-1 state separate from full scoring."""
from __future__ import annotations

from app.models.candidate_application import CandidateApplication
from app.services.pre_screening_snapshot import pre_screen_snapshot


def test_scored_candidate_keeps_genuine_pre_screen_axis():
    app = CandidateApplication(
        cv_match_score=10.0,
        pre_screen_score_100=10.0,  # contaminated legacy/shared value
        genuine_pre_screen_score_100=82.0,
        pre_screen_recommendation="Strong match",
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["cv_fit_score"] == 10.0
    assert snap["pre_screen_score"] == 82.0
    assert snap["pre_screen_recommendation"] == "Strong match"


def test_full_score_never_backfills_missing_genuine_pre_screen():
    app = CandidateApplication(
        cv_match_score=85.0,
        pre_screen_score_100=85.0,
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["cv_fit_score"] == 85.0
    assert snap["pre_screen_score"] is None


def test_unscored_candidate_keeps_genuine_pre_screen_label():
    # No cv_match score yet — the stored pre-screen verdict is authoritative.
    app = CandidateApplication(
        cv_match_score=None,
        genuine_pre_screen_score_100=70.0,
        pre_screen_recommendation="Proceed to screening",
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["pre_screen_recommendation"] == "Proceed to screening"
