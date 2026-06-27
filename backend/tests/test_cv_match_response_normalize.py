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
    _apply_self_score_requirements,
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


# ---------------------------------------------------------------------------
# Self-referential "Taali score >= N" requirements, decided at response time.
#
# These gate on the platform's own computed score (taali_score_cache_100), which
# the cv-match LLM (CV + notes only) can't evidence — so they land stored as
# "missing" even when the candidate clears the threshold. application_to_response
# decides them against the cached score so the authed candidate page renders them
# correctly without a re-score. Mirrors the grounded-report tests in
# test_grounded_top_candidates.py (search ``_self_score``).
# ---------------------------------------------------------------------------


def _req(requirement: str, status: str = "missing", **extra) -> dict:
    item = {
        "requirement_id": "crit_1",
        "requirement": requirement,
        "priority": "strong_preference",
        "status": status,
        "evidence_quotes": [],
        "impact": "",
        "reasoning": "",
    }
    item.update(extra)
    return item


def _v4_req(criterion_text: str, status: str = "missing", **extra) -> dict:
    """A cv_match_v4 row: criterion text under ``criterion_text`` (not
    ``requirement``), keyed by an integer ``criterion_id``, evidence under
    ``cv_quote``."""
    item = {
        "criterion_id": 1,
        "criterion_text": criterion_text,
        "must_have": False,
        "status": status,
        "cv_quote": None,
        "evidence_type": "absent",
        "interview_probe": "",
    }
    item.update(extra)
    return item


def test_self_score_requirement_decided_from_taali_score_on_v4_row():
    """The v4 schema renamed ``requirement`` → ``criterion_text``. The self-score
    gate must still be detected and corrected, or a v4-scored candidate's
    "Taali score >= N" criterion stays stuck on the stored "missing"."""
    details = {"requirements_assessment": [_v4_req("Taali score >= 60", status="missing")]}
    item = _apply_self_score_requirements(details, 62)["requirements_assessment"][0]
    assert item["status"] == "met"
    assert item["source"] == "taali_score"
    # The score is set on the v4 evidence field (cv_quote) as well as the v3 ones,
    # so both the candidate page and the interview kit render the verdict.
    assert item["cv_quote"] == "Taali score 62"
    assert item["evidence_quote"] == "Taali score 62"
    assert "62" in item["impact"] and "60" in item["impact"]
    # The criterion text is left untouched — only the verdict fields change.
    assert item["criterion_text"] == "Taali score >= 60"


def test_self_score_requirement_met_decided_from_taali_score():
    """The reported bug: "Taali score >= 60" stored as MISSING even though the
    candidate scored 62. Decide it against the score, not the CV."""
    details = {"requirements_assessment": [_req("Taali score >= 60", status="missing")]}
    item = _apply_self_score_requirements(details, 62)["requirements_assessment"][0]
    assert item["status"] == "met"
    assert item["source"] == "taali_score"
    # The score itself is the evidence — set on every field the candidate page reads.
    assert item["evidence"] == "Taali score 62"
    assert item["evidence_quote"] == "Taali score 62"
    assert item["evidence_quotes"] == ["Taali score 62"]
    assert "62" in item["impact"] and "60" in item["impact"]


def test_self_score_requirement_below_threshold_renders_as_gap():
    details = {"requirements_assessment": [_req("Taali score >= 60")]}
    item = _apply_self_score_requirements(details, 55)["requirements_assessment"][0]
    # "missing" is the in-enum status both candidate surfaces render as a "Gap".
    assert item["status"] == "missing"
    assert item["source"] == "taali_score"
    assert "55" in item["evidence"]
    assert "below" in item["impact"]


def test_self_score_requirement_at_most_operator_flips_direction():
    details = {"requirements_assessment": [_req("Taali score <= 40")]}
    assert _apply_self_score_requirements(details, 30)["requirements_assessment"][0]["status"] == "met"
    assert _apply_self_score_requirements(details, 55)["requirements_assessment"][0]["status"] == "missing"


def test_self_score_requirement_noop_without_score():
    """No score yet → leave the honest stored verdict, don't fabricate pass/fail."""
    details = {"requirements_assessment": [_req("Taali score >= 60", status="missing")]}
    out = _apply_self_score_requirements(details, None)
    assert out is details  # untouched object
    assert out["requirements_assessment"][0]["status"] == "missing"
    assert "source" not in out["requirements_assessment"][0]


def test_self_score_requirement_noop_for_ordinary_criteria():
    details = {
        "requirements_assessment": [
            _req("banking domain experience", status="met"),
            _req("experience with credit scoring models", status="missing"),
        ]
    }
    out = _apply_self_score_requirements(details, 80)
    assert out is details  # nothing self-referential → unchanged object
    assert out["requirements_assessment"][1]["status"] == "missing"


def test_self_score_requirement_does_not_mutate_stored_json():
    """Read-time only: the recompute must never mutate ``app.cv_match_details`` in
    place (the items are shared refs), or a later commit would persist it."""
    stored_item = _req("Taali score >= 60", status="missing")
    details = {"requirements_assessment": [stored_item]}
    out = _apply_self_score_requirements(details, 62)
    assert out["requirements_assessment"][0]["status"] == "met"
    # The stored objects are left exactly as they were.
    assert stored_item["status"] == "missing"
    assert "source" not in stored_item
    assert details["requirements_assessment"][0] is stored_item


def test_self_score_requirement_only_self_referential_row_changes():
    details = {
        "requirements_assessment": [
            _req("Taali score >= 60", status="missing", requirement_id="crit_1"),
            _req("kafka", status="missing", requirement_id="crit_2"),
        ]
    }
    by_id = {
        i["requirement_id"]: i
        for i in _apply_self_score_requirements(details, 70)["requirements_assessment"]
    }
    assert by_id["crit_1"]["status"] == "met"
    assert by_id["crit_2"]["status"] == "missing"  # untouched
    assert "source" not in by_id["crit_2"]


def test_self_score_shared_helpers_classify_and_decide():
    """The detection/threshold/decision logic shared with the grounded report."""
    from app.candidate_search.self_score import (
        is_self_score_criterion,
        parse_score_threshold,
        self_score_decision,
    )

    assert is_self_score_criterion("Taali score >= 60")
    assert is_self_score_criterion("minimum Taali score 55")
    assert not is_self_score_criterion("experience with scoring models")
    assert not is_self_score_criterion("Taali platform experience")  # no number/score token
    assert parse_score_threshold("Taali score >= 60") == ("geq", 60.0)
    assert parse_score_threshold("Taali score 70") == ("geq", 70.0)  # bare → floor
    assert parse_score_threshold("Taali score <= 40") == ("leq", 40.0)
    assert self_score_decision("Taali score >= 60", 62) == (True, "geq", 60.0)
    assert self_score_decision("Taali score >= 60", 55) == (False, "geq", 60.0)
    assert self_score_decision("Taali score >= 60", None) is None
    assert self_score_decision("banking experience", 90) is None
