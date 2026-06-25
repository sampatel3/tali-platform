"""The holistic engine's CV-integrity revival (2026-06-25).

Timeline + unverified-claim signals are computed and PERSISTED on every
holistic score; the bounded penalty is only DEDUCTED when
``HOLISTIC_INTEGRITY_PENALTY_ENABLED`` (so it ships in shadow first). Also
covers the opt-in hidden-text hard cap.
"""

from __future__ import annotations

from app.cv_matching.holistic import (
    _cap_for_hidden_text,
    _Claim,
    _Derivation,
    _LeanScore,
    _Report,
    _Snapshot,
    _to_output,
    _TL,
)
from app.platform.config import settings


def _report_with_issues() -> _Report:
    return _Report(
        snapshot=_Snapshot(
            years_experience=5,
            top_skills=["python"],
            # end before start → one timeline issue
            timeline=[_TL(company="Acme", role="Eng", start_year=2020, end_year=2018)],
        ),
        claims=[
            _Claim(
                claim_text="1st place Imaginary Global Hackathon 2099",
                claim_type="competition",
                corroboration="uncorroborated",
                model_familiarity="implausible",
                reasoning="no supporting detail",
            )
        ],
    )


def _score() -> _LeanScore:
    return _LeanScore(overall=80, verdict="Solid", reasoning="fits")


def test_integrity_shadow_persists_flags_without_deducting(monkeypatch):
    monkeypatch.setattr(settings, "HOLISTIC_INTEGRITY_PENALTY_ENABLED", False)
    out = _to_output(_score(), _report_with_issues(), _Derivation(), "trace", None, None)

    # Score is untouched in shadow mode...
    assert out.role_fit_score == 80
    assert out.integrity_penalty == 0.0
    # ...but the signals are persisted for the recruiter / shadow analysis.
    assert out.timeline_flags  # the end-before-start issue
    assert len(out.claims_to_verify) == 1
    assert out.integrity_signals["applied"] is False
    assert out.integrity_signals["penalty_computed"] > 0


def test_integrity_applied_deducts_consistently(monkeypatch):
    monkeypatch.setattr(settings, "HOLISTIC_INTEGRITY_PENALTY_ENABLED", True)
    out = _to_output(_score(), _report_with_issues(), _Derivation(), "trace", None, None)

    assert out.integrity_penalty > 0
    assert out.role_fit_score == 80 - out.integrity_penalty
    # cv_fit / requirements_match kept == role_fit so a downstream
    # 0.40·cv_fit + 0.60·req recomposition returns the same penalised score.
    assert out.cv_fit_score == out.role_fit_score
    assert out.requirements_match_score == out.role_fit_score
    assert out.integrity_signals["applied"] is True


def test_clean_cv_has_no_penalty(monkeypatch):
    monkeypatch.setattr(settings, "HOLISTIC_INTEGRITY_PENALTY_ENABLED", True)
    clean = _Report(
        snapshot=_Snapshot(
            years_experience=5,
            timeline=[_TL(company="Acme", role="Eng", start_year=2018, end_year=2021)],
        )
    )
    out = _to_output(_score(), clean, _Derivation(), "trace", None, None)
    assert out.integrity_penalty == 0.0
    assert out.role_fit_score == 80
    assert out.timeline_flags == []


def test_cap_for_hidden_text_drops_all_score_fields(monkeypatch):
    monkeypatch.setattr(settings, "HOLISTIC_INTEGRITY_PENALTY_ENABLED", False)
    out = _to_output(_score(), _report_with_issues(), _Derivation(), "trace", None, None)
    capped = _cap_for_hidden_text(out, 10.0)
    assert capped.role_fit_score == 10.0
    assert capped.cv_fit_score == 10.0
    assert capped.requirements_match_score == 10.0
    assert capped.integrity_signals["document_hygiene"]["action"] == "capped"
    assert "hidden text" in capped.summary.lower()
