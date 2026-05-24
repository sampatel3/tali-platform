"""pre_screen_snapshot must keep the displayed recommendation consistent with
the authoritative score for scored candidates — no stale over-optimistic
labels (e.g. "Strong match" surviving on a candidate the full score puts at 10).
"""
from __future__ import annotations

from app.models.candidate_application import CandidateApplication
from app.services.pre_screening_snapshot import pre_screen_snapshot


def test_scored_candidate_label_tracks_score_not_stale_stored():
    # Stale "Strong match" stored, but the real cv_match score is 10.
    app = CandidateApplication(
        cv_match_score=10.0,
        pre_screen_recommendation="Strong match",
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["pre_screen_recommendation"] == "Below threshold"
    assert snap["pre_screen_recommendation"] != "Strong match"


def test_scored_candidate_label_strong_when_score_high():
    app = CandidateApplication(
        cv_match_score=85.0,
        pre_screen_recommendation="Below threshold",  # stale pessimistic
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["pre_screen_recommendation"] == "Strong match"


def test_unscored_candidate_keeps_genuine_pre_screen_label():
    # No cv_match score yet — the stored pre-screen verdict is authoritative.
    app = CandidateApplication(
        cv_match_score=None,
        pre_screen_recommendation="Proceed to screening",
        cv_match_details={},
    )
    snap = pre_screen_snapshot(app)
    assert snap["pre_screen_recommendation"] == "Proceed to screening"
