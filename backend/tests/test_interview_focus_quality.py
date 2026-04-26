"""Tests for the interview-focus quality signals (P2 fix).

The auditor flagged that ``_normalize_focus`` silently padded malformed
Claude output with default fallback questions. These tests verify that the
normalized payload now exposes ``quality_status`` and ``fallback_used`` so
callers can decide whether to surface a regenerate prompt or fall back to
default content with a clear caveat.
"""

from __future__ import annotations

from app.services.interview_focus_service import _normalize_focus


def _question(text: str) -> dict:
    return {
        "question": text,
        "what_to_listen_for": ["concrete metrics"],
        "concerning_signals": ["vague claims"],
    }


def test_status_ok_when_three_role_specific_questions() -> None:
    payload = {
        "role_summary": "Senior backend role on the payments team.",
        "manual_screening_triggers": ["mention of payments"],
        "questions": [_question(f"Q{i}") for i in range(3)],
    }
    out = _normalize_focus(payload)
    assert out["quality_status"] == "ok"
    assert out["fallback_used"] is False
    assert out["role_specific_question_count"] == 3
    assert len(out["questions"]) == 3


def test_status_partial_when_some_questions_missing() -> None:
    payload = {
        "role_summary": "Senior backend.",
        "manual_screening_triggers": [],
        "questions": [_question("Only one question")],
    }
    out = _normalize_focus(payload)
    assert out["quality_status"] == "partial"
    assert out["fallback_used"] is True
    assert out["role_specific_question_count"] == 1
    assert len(out["questions"]) == 3, "fallback questions still pad the visible list"


def test_status_failed_when_no_role_specific_questions() -> None:
    out = _normalize_focus({"questions": []})
    assert out["quality_status"] == "failed"
    assert out["fallback_used"] is True
    assert out["role_specific_question_count"] == 0


def test_status_failed_when_questions_field_is_not_a_list() -> None:
    out = _normalize_focus({"questions": "not a list"})
    assert out["quality_status"] == "failed"
    assert out["fallback_used"] is True


def test_normalized_payload_drops_blank_questions_before_counting() -> None:
    payload = {
        "questions": [
            _question(""),  # empty question text — should be skipped
            _question("Real question"),
        ],
    }
    out = _normalize_focus(payload)
    assert out["role_specific_question_count"] == 1
    assert out["quality_status"] == "partial"
