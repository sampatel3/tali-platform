"""WS3: score-time integrity wiring.

Covers (2) supplementary flag-only signals merged into integrity_signals, (3) the
ingest-time PDF hygiene stash promoted into integrity_signals.document_hygiene.pdf,
and (1) the penalty now applied by DEFAULT (config flip) with the before/after
delta logged.
"""

from __future__ import annotations

import logging

from app.services.cv_score_orchestrator import _augment_integrity_signals


class _Cand:
    def __init__(self, cv_sections=None, experience_entries=None):
        self.cv_sections = cv_sections
        self.experience_entries = experience_entries


class _App:
    def __init__(self, candidate=None, cv_sections=None):
        self.candidate = candidate
        self.cv_sections = cv_sections


_JD = (
    "We need a data engineer to build and own our Spark and Airflow batch "
    "pipelines end to end, partner with product, and mentor juniors on the team."
)


def test_supplementary_signals_merged_into_integrity_signals():
    # A CV whose sections carry an unverified employer → the supplementary bundle
    # (jd_shingle + unverified_employers) is merged and triangulated. Flag-only.
    cv_sections = {
        "experience": [
            {"company": "Ghost Corp", "company_unverified": True,
             "start": "2019", "end": "2021"},
        ]
    }
    app = _App(candidate=_Cand(cv_sections=cv_sections), cv_sections=cv_sections)
    out = _augment_integrity_signals(
        {"applied": True, "penalty_computed": 0.0},
        app, cv_text="Some CV text describing pipelines.", job_spec_text=_JD,
        snapshot={"years_experience": 4, "timeline": []},
    )
    assert isinstance(out, dict)
    # jd_shingle always computed; unverified_employers surfaced from cv_sections.
    assert "jd_shingle" in out
    assert out["unverified_employers"]["count"] == 1
    assert out["unverified_employers"]["companies"] == ["Ghost Corp"]
    # Triangulation + warnings composed server-side.
    assert "triangulation" in out and "warnings" in out
    # The pre-existing score-time flags are preserved (flag-only merge).
    assert out["applied"] is True


def test_pdf_hygiene_promoted_under_document_hygiene():
    app = _App(candidate=_Cand(), cv_sections={})
    pdf = {"triggered": True, "metadata": {"checked": True, "metadata_keyword_stuffing": True},
           "render": {"checked": True, "triggered": False}}
    out = _augment_integrity_signals(
        {}, app, cv_text="cv", job_spec_text=_JD, snapshot={}, pdf_hygiene=pdf,
    )
    assert out["document_hygiene"]["pdf"] == pdf


def test_pdf_hygiene_preserves_existing_text_hygiene():
    app = _App(candidate=_Cand(), cv_sections={})
    existing = {"document_hygiene": {"injection_detected": True, "has_tag_chars": False}}
    pdf = {"triggered": False}
    out = _augment_integrity_signals(
        existing, app, cv_text="cv", job_spec_text=_JD, snapshot={}, pdf_hygiene=pdf,
    )
    # Both the LLM-path text hygiene and the new bytes-level pdf sub-key survive.
    assert out["document_hygiene"]["injection_detected"] is True
    assert out["document_hygiene"]["pdf"] == pdf


def test_penalty_applied_by_default_and_delta_logged(caplog):
    # WS3 flipped HOLISTIC_INTEGRITY_PENALTY_ENABLED default → True. A holistic
    # score with a real timeline issue now deducts AND logs the before/after.
    from app.cv_matching.holistic import (
        _Claim, _Derivation, _LeanScore, _Report, _Snapshot, _to_output, _TL,
    )
    from app.platform.config import settings

    assert settings.HOLISTIC_INTEGRITY_PENALTY_ENABLED is True  # default is now on

    report = _Report(
        snapshot=_Snapshot(
            years_experience=5,
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
    score = _LeanScore(overall=80, verdict="Solid", reasoning="fits")
    with caplog.at_level(logging.INFO, logger="app.cv_matching.holistic"):
        out = _to_output(score, report, _Derivation(), "trace-xyz", None, None)

    assert out.integrity_penalty > 0
    assert out.role_fit_score == 80 - out.integrity_penalty
    assert out.integrity_signals["applied"] is True
    # The before/after delta line was logged for the audit trail.
    line = next((r.getMessage() for r in caplog.records if "integrity penalty applied" in r.getMessage()), None)
    assert line is not None
    assert "trace-xyz" in line and "pre_penalty=80.00" in line
