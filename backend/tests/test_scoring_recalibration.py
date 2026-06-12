"""Scoring recalibration: requirements_match v2 + derived-criteria cap.

v2 fixes the calibration gap where candidates who meet every must-have still
scored low — driven by (a) "unknown" requirements penalised as near-missing,
and (b) a long preference tail outweighing the must-haves.
"""

from __future__ import annotations

from app.cv_matching.aggregation import (
    compute_requirements_match_score,
    compute_requirements_match_score_v1,
    compute_requirements_match_score_v2,
)
from app.cv_matching.schemas import (
    Confidence,
    Priority,
    RequirementAssessment,
    Status,
)
from app.services.spec_normalizer import MAX_DERIVED_CRITERIA, derive_criteria

MUST = Priority.MUST_HAVE
PREF = Priority.STRONG_PREFERENCE


def _ra(rid: str, priority: Priority, status: Status, *, match_tier: str = "exact"):
    return RequirementAssessment(
        requirement_id=rid,
        requirement=rid,
        priority=priority,
        evidence_quotes=["x"] if status == Status.MET else [],
        evidence_start_char=0,
        evidence_end_char=1,
        reasoning="",
        status=status,
        match_tier=match_tier if status in (Status.MET, Status.PARTIALLY_MET) else "missing",
        impact="",
        confidence=Confidence.MEDIUM,
    )


def _prefs(n, status, start=1):
    return [_ra(f"jd_req_{i}", PREF, status) for i in range(start, start + n)]


# --------------------------------------------------------------------------
# v2 requirements_match
# --------------------------------------------------------------------------


def test_v2_does_not_penalise_unknown_like_v1():
    # Mehran-shaped: 1 must met, 3 pref met, 7 partial, 1 missing, 9 unknown.
    reqs = (
        [_ra("jd_req_0", MUST, Status.MET)]
        + _prefs(3, Status.MET, 1)
        + _prefs(7, Status.PARTIALLY_MET, 10)
        + _prefs(1, Status.MISSING, 20)
        + _prefs(9, Status.UNKNOWN, 30)
    )
    v1 = compute_requirements_match_score_v1(reqs)
    v2 = compute_requirements_match_score_v2(reqs)
    # v1 sinks him to the low 30s; v2 lifts substantially (unknowns no longer 0.3).
    assert v2 > v1 + 20


def test_v2_mostly_unknown_one_must_lands_near_prior_not_high():
    # 1 must met + 50 UNKNOWN prefs -> coverage guard pulls toward ~50, NOT ~100.
    reqs = [_ra("jd_req_0", MUST, Status.MET)] + _prefs(50, Status.UNKNOWN)
    assert 45 <= compute_requirements_match_score_v2(reqs) <= 62


def test_v2_must_have_not_drowned_by_many_preferences():
    # 1 must met + 50 prefs MISSING. v1 drowns the must (~3); v2 keeps it up.
    reqs = [_ra("jd_req_0", MUST, Status.MET)] + _prefs(50, Status.MISSING)
    v1 = compute_requirements_match_score_v1(reqs)
    v2 = compute_requirements_match_score_v2(reqs)
    assert v1 < 10  # must-have drowned under the flat average
    assert v2 >= 60  # count-normalisation gives the met must-have a fixed 0.65 share


def test_v2_missing_must_have_drags_hard():
    # must-have MISSING + all prefs met -> soft constraint pulls down.
    reqs = [_ra("jd_req_0", MUST, Status.MISSING)] + _prefs(5, Status.MET)
    assert compute_requirements_match_score_v2(reqs) < 45


def test_v2_partial_must_have_gets_partial_credit():
    miss = compute_requirements_match_score_v2([_ra("m", MUST, Status.MISSING)])
    part = compute_requirements_match_score_v2([_ra("m", MUST, Status.PARTIALLY_MET)])
    full = compute_requirements_match_score_v2([_ra("m", MUST, Status.MET)])
    assert miss < part < full


def test_v2_all_met_is_high():
    reqs = [_ra("jd_req_0", MUST, Status.MET)] + _prefs(5, Status.MET)
    assert compute_requirements_match_score_v2(reqs) >= 95


def test_flag_dispatch(monkeypatch):
    reqs = [_ra("jd_req_0", MUST, Status.MET)] + _prefs(5, Status.UNKNOWN)
    monkeypatch.delenv("CV_MATCH_REQ_MATCH_V2", raising=False)
    assert compute_requirements_match_score(reqs) == compute_requirements_match_score_v1(reqs)
    monkeypatch.setenv("CV_MATCH_REQ_MATCH_V2", "on")
    assert compute_requirements_match_score(reqs) == compute_requirements_match_score_v2(reqs)


# --------------------------------------------------------------------------
# derived-criteria cap (10, must-have-safe)
# --------------------------------------------------------------------------


def test_derived_criteria_capped_at_10():
    jd = "\n".join(f"Experience with tool number {i}" for i in range(30))
    out = derive_criteria(jd)
    assert len(out) <= MAX_DERIVED_CRITERIA == 10


def test_cap_never_drops_must_haves():
    # 8 explicit must-haves + 20 preferences; the cap must keep all must-haves
    # and clamp only the preference tail.
    musts = "\n".join(f"Must have skill {i}" for i in range(8))
    prefs = "\n".join(f"Nice to have exposure to {i}" for i in range(20))
    jd = f"Must have:\n{musts}\nNice to have:\n{prefs}"
    out = derive_criteria(jd)
    must_kept = sum(1 for c in out if c.bucket == "must")
    assert must_kept == 8  # all must-haves survive the cap
    assert len(out) <= 10  # preference tail clamped to fit
