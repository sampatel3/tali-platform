"""Tests for tolerant enum coercion on CV match schemas.

The LLM frequently paraphrases enum values (`"preferred"` for
`strong_preference`, `"must have"` for `must_have`, etc.). Recruiters
typing values in the admin UI make the same kind of slips. The
``field_validator`` coercion in ``schemas.py`` should accept the
common variants and only fail on truly unrecognisable values.
"""

from __future__ import annotations

import pytest

from app.cv_matching.schemas import (
    Confidence,
    Priority,
    RequirementAssessment,
    RequirementInput,
    Status,
)


def _build_assessment(**overrides):
    """Helper: minimal valid assessment payload, override one field per test."""
    base = {
        "requirement_id": "jd_req_1",
        "requirement": "x",
        "priority": "must_have",
        "evidence_quotes": ["x"],
        "status": "met",
        "match_tier": "exact",
        "confidence": "high",
    }
    base.update(overrides)
    return RequirementAssessment.model_validate(base)


# --- priority -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("must_have", Priority.MUST_HAVE),
        ("must have", Priority.MUST_HAVE),
        ("must-have", Priority.MUST_HAVE),
        ("MUST_HAVE", Priority.MUST_HAVE),
        ("Must Have", Priority.MUST_HAVE),
        ("musthave", Priority.MUST_HAVE),
        ("required", Priority.MUST_HAVE),
        ("mandatory", Priority.MUST_HAVE),
        ("essential", Priority.MUST_HAVE),
        ("strong_preference", Priority.STRONG_PREFERENCE),
        ("strong preference", Priority.STRONG_PREFERENCE),
        ("preferred", Priority.STRONG_PREFERENCE),
        ("Preferred", Priority.STRONG_PREFERENCE),
        ("desirable", Priority.STRONG_PREFERENCE),
        ("nice_to_have", Priority.NICE_TO_HAVE),
        ("nice to have", Priority.NICE_TO_HAVE),
        ("nice-to-have", Priority.NICE_TO_HAVE),
        ("optional", Priority.NICE_TO_HAVE),
        ("bonus", Priority.NICE_TO_HAVE),
        ("constraint", Priority.CONSTRAINT),
        ("constraints", Priority.CONSTRAINT),
        ("disqualifying", Priority.CONSTRAINT),
    ],
)
def test_priority_coercion(raw, expected):
    a = _build_assessment(priority=raw)
    assert a.priority == expected


def test_priority_invalid_still_errors():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        _build_assessment(priority="urgent_priority_level_5")


def test_priority_coercion_on_recruiter_input():
    """Same coercion applies on RequirementInput (recruiter-typed)."""
    r = RequirementInput.model_validate(
        {"id": "r1", "requirement": "Python", "priority": "preferred"}
    )
    assert r.priority == Priority.STRONG_PREFERENCE


# --- status ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("met", Status.MET),
        ("MET", Status.MET),
        ("matches", Status.MET),
        ("satisfied", Status.MET),
        ("yes", Status.MET),
        ("partially_met", Status.PARTIALLY_MET),
        ("partially met", Status.PARTIALLY_MET),
        ("partial", Status.PARTIALLY_MET),
        ("partially", Status.PARTIALLY_MET),
        ("missing", Status.MISSING),
        ("not_met", Status.MISSING),
        ("not met", Status.MISSING),
        ("absent", Status.MISSING),
        ("no", Status.MISSING),
        ("unknown", Status.UNKNOWN),
        ("uncertain", Status.UNKNOWN),
        ("n/a", Status.UNKNOWN),
        ("not applicable", Status.UNKNOWN),
        ("no evidence", Status.UNKNOWN),
    ],
)
def test_status_coercion(raw, expected):
    # Status is the field that drives evidence requirements; for missing/
    # unknown we also need match_tier to align.
    extra = {}
    if raw.lower().strip() in ("missing", "not_met", "not met", "absent", "no", "unknown", "uncertain", "n/a", "not applicable", "no evidence"):
        extra["match_tier"] = "missing"
        extra["evidence_quotes"] = []
    a = _build_assessment(status=raw, **extra)
    assert a.status == expected


# --- match_tier -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("exact", "exact"),
        ("EXACT", "exact"),
        ("perfect", "exact"),
        ("perfect match", "exact"),
        ("strong_substitute", "strong_substitute"),
        ("strong substitute", "strong_substitute"),
        ("close match", "strong_substitute"),
        ("similar", "strong_substitute"),
        ("equivalent", "strong_substitute"),
        ("weak_substitute", "weak_substitute"),
        ("weak substitute", "weak_substitute"),
        ("loose match", "weak_substitute"),
        ("tangential", "weak_substitute"),
        ("unrelated", "unrelated"),
        ("not related", "unrelated"),
        ("off-topic", "unrelated"),
        ("irrelevant", "unrelated"),
    ],
)
def test_match_tier_coercion(raw, expected):
    a = _build_assessment(match_tier=raw)
    assert a.match_tier == expected


def test_match_tier_missing_with_aligned_status():
    a = _build_assessment(
        status="missing", match_tier="none", evidence_quotes=[]
    )
    assert a.match_tier == "missing"


# --- confidence -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("high", Confidence.HIGH),
        ("HIGH", Confidence.HIGH),
        ("strong", Confidence.HIGH),
        ("confident", Confidence.HIGH),
        ("medium", Confidence.MEDIUM),
        ("moderate", Confidence.MEDIUM),
        ("med", Confidence.MEDIUM),
        ("average", Confidence.MEDIUM),
        ("low", Confidence.LOW),
        ("weak", Confidence.LOW),
        ("uncertain", Confidence.LOW),
    ],
)
def test_confidence_coercion(raw, expected):
    a = _build_assessment(confidence=raw)
    assert a.confidence == expected


# --- whitespace + case insensitivity --------------------------------------


def test_coercion_strips_whitespace_and_ignores_case():
    a = _build_assessment(priority="  Must Have  ")
    assert a.priority == Priority.MUST_HAVE
    a = _build_assessment(status=" PARTIALLY MET ")
    assert a.status == Status.PARTIALLY_MET
