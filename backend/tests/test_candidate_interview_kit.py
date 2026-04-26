"""Tests for the deterministic candidate interview kit aggregator."""

from __future__ import annotations

from app.services.candidate_interview_kit import (
    build_candidate_interview_kit_from_details,
)


def _entry(**overrides):
    base = {
        "criterion_id": 1,
        "criterion_text": "Sample criterion",
        "must_have": False,
        "blocker": False,
        "status": "met",
        "confidence": 0.9,
        "evidence_type": "explicit",
        "cv_quote": "verbatim quote",
        "risk_level": "low",
        "screening_recommendation": "advance",
        "interview_probe": "Probe question?",
    }
    base.update(overrides)
    return base


def _details(entries: list[dict]) -> dict:
    return {
        "scoring_version": "cv_match_v4",
        "requirements_assessment": entries,
    }


def test_returns_none_for_non_v4_details() -> None:
    assert build_candidate_interview_kit_from_details(None) is None
    assert build_candidate_interview_kit_from_details({}) is None
    assert build_candidate_interview_kit_from_details(
        {"scoring_version": "cv_fit_v3_evidence_enriched", "requirements_assessment": [_entry()]}
    ) is None


def test_returns_none_when_no_assessments() -> None:
    assert build_candidate_interview_kit_from_details(_details([])) is None


def test_groups_into_blockers_priority_probes_and_confirmed() -> None:
    entries = [
        _entry(criterion_id=10, must_have=True, blocker=True, status="missing", evidence_type="absent", confidence=0.1),
        _entry(criterion_id=11, must_have=True, status="partially_met", evidence_type="implied", confidence=0.4),
        _entry(criterion_id=12, must_have=False, status="met", confidence=0.92),
        _entry(criterion_id=13, must_have=False, status="met", confidence=0.55),  # low conf → priority
    ]
    kit = build_candidate_interview_kit_from_details(_details(entries))
    assert kit is not None

    knockout_ids = [item["criterion_id"] for item in kit["knockout_checks"]]
    probe_ids = [item["criterion_id"] for item in kit["priority_probes"]]
    confirmed_ids = [item["criterion_id"] for item in kit["confirmed_strengths"]]

    assert knockout_ids == [10]
    assert 11 in probe_ids
    assert 13 in probe_ids  # low confidence should bubble into probes
    assert confirmed_ids == [12]


def test_must_have_partial_outranks_optional_low_confidence() -> None:
    entries = [
        _entry(criterion_id=20, must_have=False, status="met", confidence=0.4),
        _entry(criterion_id=21, must_have=True, status="partially_met", confidence=0.55),
    ]
    kit = build_candidate_interview_kit_from_details(_details(entries))
    assert kit is not None
    probe_ids = [item["criterion_id"] for item in kit["priority_probes"]]
    assert probe_ids[0] == 21, "must_have partials should sort before optional low-confidence rows"


def test_summary_counts_match_groupings() -> None:
    entries = [
        _entry(criterion_id=1, must_have=True, blocker=True, status="missing", confidence=0.1),
        _entry(criterion_id=2, must_have=True, status="partially_met", confidence=0.4),
        _entry(criterion_id=3, must_have=False, status="met", confidence=0.9),
    ]
    kit = build_candidate_interview_kit_from_details(_details(entries))
    assert kit is not None
    assert kit["summary"] == {
        "total_criteria": 3,
        "blockers": 1,
        "needs_probing": 1,
        "confirmed": 1,
    }


def test_kit_item_carries_through_evidence_fields() -> None:
    entry = _entry(
        criterion_id=99,
        must_have=True,
        status="partially_met",
        evidence_type="implied",
        cv_quote="led the platform team",
        interview_probe="Ask for headcount and reporting lines.",
        risk_level="med",
        screening_recommendation="borderline",
        confidence=0.5,
    )
    kit = build_candidate_interview_kit_from_details(_details([entry]))
    assert kit is not None
    assert kit["priority_probes"][0]["interview_probe"] == "Ask for headcount and reporting lines."
    assert kit["priority_probes"][0]["evidence_type"] == "implied"
    assert kit["priority_probes"][0]["risk_level"] == "med"
