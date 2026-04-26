"""Tests for cv_match_v4 schema validation and quote verification.

These tests exercise the pure-function pieces of the v4 path. The Claude API
call itself is not exercised here — wiring tests live in test_api_roles and
the workable sync tests, where they use mocks/monkeypatching.
"""

from __future__ import annotations

import pytest

from app.services.fit_matching_service import (
    CV_MATCH_V4_PROMPT_VERSION,
    CvMatchValidationError,
    _format_criteria_block,
    _validate_v4_payload,
    _verify_quote_in_cv,
)


CV_TEXT = """Jane Doe
Senior Backend Engineer

Experience:
- Led a team of 6 engineers building a payments platform on AWS.
- Shipped Postgres-backed services handling 12M requests/day.
- Mentored two junior engineers and ran weekly architecture reviews.
"""


CRITERIA = [
    {"id": 11, "text": "5+ years Python", "must_have": True, "source": "recruiter"},
    {"id": 12, "text": "AWS experience", "must_have": True, "source": "recruiter"},
    {"id": 13, "text": "Mentorship", "must_have": False, "source": "derived_from_spec"},
]


def _base_payload() -> dict:
    return {
        "overall_match_score": 82,
        "skills_match_score": 80,
        "experience_relevance_score": 85,
        "requirements_match_score": 78,
        "recommendation": "yes",
        "summary": "Strong fit; clear AWS and team-leadership signal.",
        "matching_skills": ["Python", "AWS", "Postgres"],
        "missing_skills": [],
        "experience_highlights": ["Led 6-engineer team", "12M req/day Postgres service"],
        "concerns": [],
        "requirements_assessment": [
            {
                "criterion_id": 11,
                "status": "met",
                "confidence": 0.85,
                "cv_quote": "Senior Backend Engineer",
                "evidence_type": "explicit",
                "blocker": False,
                "risk_level": "low",
                "screening_recommendation": "advance",
                "interview_probe": "Walk me through the most complex Python service you've owned.",
            },
            {
                "criterion_id": 12,
                "status": "met",
                "confidence": 0.9,
                "cv_quote": "payments platform on AWS",
                "evidence_type": "explicit",
                "blocker": False,
                "risk_level": "low",
                "screening_recommendation": "advance",
                "interview_probe": "Which AWS services did you operate end-to-end?",
            },
            {
                "criterion_id": 13,
                "status": "met",
                "confidence": 0.7,
                "cv_quote": "Mentored two junior engineers",
                "evidence_type": "explicit",
                "blocker": False,
                "risk_level": "low",
                "screening_recommendation": "advance",
                "interview_probe": "Describe how you structure mentorship 1:1s.",
            },
        ],
    }


def test_quote_verifier_accepts_verbatim_substring() -> None:
    assert _verify_quote_in_cv("payments platform on AWS", CV_TEXT) == "payments platform on AWS"


def test_quote_verifier_is_case_and_whitespace_insensitive() -> None:
    assert _verify_quote_in_cv("Payments  Platform   on   aws", CV_TEXT) == "Payments  Platform   on   aws"


def test_quote_verifier_rejects_quote_not_in_cv() -> None:
    assert _verify_quote_in_cv("led a 50 engineer team", CV_TEXT) is None


def test_quote_verifier_rejects_empty_or_none() -> None:
    assert _verify_quote_in_cv(None, CV_TEXT) is None
    assert _verify_quote_in_cv("", CV_TEXT) is None
    assert _verify_quote_in_cv("   ", CV_TEXT) is None


def test_quote_verifier_truncates_overlong_quotes() -> None:
    long_quote = "Senior Backend Engineer" + " x" * 500
    # Truncation happens before substring check; truncated form likely fails.
    # The contract: never return >200 chars.
    result = _verify_quote_in_cv(long_quote, CV_TEXT)
    assert result is None or len(result) <= 200


def test_validator_accepts_well_formed_payload() -> None:
    validated = _validate_v4_payload(_base_payload(), criteria=CRITERIA, cv_text=CV_TEXT)
    assert validated["overall_match_score"] == 82.0
    assert validated["recommendation"] == "yes"
    assert len(validated["requirements_assessment"]) == 3
    ids = [a["criterion_id"] for a in validated["requirements_assessment"]]
    assert ids == [11, 12, 13]


def test_validator_drops_unverifiable_quote_and_marks_absent() -> None:
    payload = _base_payload()
    payload["requirements_assessment"][0]["cv_quote"] = "led a 50 engineer team"
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    first = validated["requirements_assessment"][0]
    assert first["cv_quote"] is None
    assert first["evidence_type"] == "absent"


def test_validator_rejects_unknown_criterion_id() -> None:
    payload = _base_payload()
    payload["requirements_assessment"][0]["criterion_id"] = 999
    with pytest.raises(CvMatchValidationError) as exc:
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    assert "999" in exc.value.reason


def test_validator_rejects_duplicate_criterion_id() -> None:
    payload = _base_payload()
    payload["requirements_assessment"].append(payload["requirements_assessment"][0].copy())
    with pytest.raises(CvMatchValidationError) as exc:
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    assert "more than once" in exc.value.reason


def test_validator_rejects_missing_criterion_entries() -> None:
    payload = _base_payload()
    payload["requirements_assessment"] = payload["requirements_assessment"][:2]
    with pytest.raises(CvMatchValidationError) as exc:
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    assert "13" in exc.value.reason


def test_validator_rejects_invalid_status() -> None:
    payload = _base_payload()
    payload["requirements_assessment"][0]["status"] = "totally_met"
    with pytest.raises(CvMatchValidationError):
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)


def test_validator_clamps_confidence() -> None:
    payload = _base_payload()
    payload["requirements_assessment"][0]["confidence"] = 1.7
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    assert validated["requirements_assessment"][0]["confidence"] == 1.0


def test_validator_downgrades_blocker_for_non_must_have() -> None:
    payload = _base_payload()
    # criterion_id 13 is must_have=False; if the model marks it blocker, we drop.
    payload["requirements_assessment"][2]["blocker"] = True
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    nice_to_have = next(a for a in validated["requirements_assessment"] if a["criterion_id"] == 13)
    assert nice_to_have["blocker"] is False


def test_validator_keeps_blocker_for_must_have_when_missing() -> None:
    payload = _base_payload()
    payload["requirements_assessment"][1]["status"] = "missing"
    payload["requirements_assessment"][1]["blocker"] = True
    payload["requirements_assessment"][1]["evidence_type"] = "absent"
    payload["requirements_assessment"][1]["cv_quote"] = None
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    aws_entry = next(a for a in validated["requirements_assessment"] if a["criterion_id"] == 12)
    assert aws_entry["blocker"] is True
    assert aws_entry["status"] == "missing"


def test_validator_rejects_invalid_recommendation() -> None:
    payload = _base_payload()
    payload["recommendation"] = "definitely_yes"
    with pytest.raises(CvMatchValidationError):
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)


def test_validator_rejects_non_object_payload() -> None:
    with pytest.raises(CvMatchValidationError):
        _validate_v4_payload("not a dict", criteria=CRITERIA, cv_text=CV_TEXT)


def test_validator_rejects_missing_overall_score() -> None:
    payload = _base_payload()
    del payload["overall_match_score"]
    with pytest.raises(CvMatchValidationError) as exc:
        _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT)
    assert "overall" in exc.value.reason


def test_format_criteria_block_includes_must_have_flag_and_id() -> None:
    block = _format_criteria_block(CRITERIA)
    assert "criterion_id=11" in block
    assert "[must_have]" in block
    assert "criterion_id=13" in block
    assert "derived_from_spec" in block


def test_format_criteria_block_handles_empty_list() -> None:
    block = _format_criteria_block([])
    assert "none" in block.lower()


def test_prompt_version_constant() -> None:
    assert CV_MATCH_V4_PROMPT_VERSION == "cv_match_v4"
