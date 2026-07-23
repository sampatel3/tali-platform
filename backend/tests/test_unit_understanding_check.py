"""Unit tests for the post-submit understanding check.

Everything here is deterministic — generation is the only part that calls
Anthropic, and it is not exercised. Tests pin:
- question-set validation (the gate between a model response and the DB)
- the candidate-facing view NEVER leaking the correct answer
- one-at-a-time serving and the server-side per-question deadline
- scoring over the questions ASKED, not the questions answered
- window open/close/skip semantics, including idempotent close
- the ``comprehension_outcome`` grader's buckets and its not-assessed path
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.components.assessments import understanding_check as uc
from app.components.assessments.rubric_scoring import RubricScorer, ScoringArtifacts


def _question(qid: str = "q1", correct: int = 2) -> Dict[str, Any]:
    return {
        "id": qid,
        "prompt": f"In {qid}, which function normalizes the payload?",
        "options": ["_coerce", "_normalize", "_flatten", "_expand"],
        "correct_index": correct,
        "probe": "locate",
        "evidence": "src/ingest.py:41-58",
        "rationale": "It is the only one that touches the payload dict.",
    }


def _assessment(questions: List[Dict[str, Any]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        understanding_check_status=None,
        understanding_check_questions=questions,
        understanding_check_answers=None,
        understanding_check_score=None,
        understanding_check_started_at=None,
        understanding_check_expires_at=None,
        understanding_check_completed_at=None,
    )


# --- validation ------------------------------------------------------------


def test_valid_question_set_passes():
    assert uc.validate_questions([_question()]) == []


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"options": ["a", "b", "c"]}, "exactly 4"),
        ({"options": ["a", "a", "b", "c"]}, "distinct"),
        ({"correct_index": 4}, "correct_index"),
        ({"correct_index": "2"}, "correct_index"),
        ({"probe": "vibes"}, "probe"),
        ({"prompt": "  "}, "prompt"),
        ({"evidence": ""}, "evidence"),
    ],
)
def test_invalid_question_is_rejected(mutation, fragment):
    question = {**_question(), **mutation}
    errors = uc.validate_questions([question])
    assert any(fragment in error for error in errors), errors


def test_duplicate_ids_rejected():
    errors = uc.validate_questions([_question("q1"), _question("q1")])
    assert any("duplicated" in error for error in errors)


def test_empty_or_non_list_rejected():
    assert uc.validate_questions([]) == ["questions must be non-empty"]
    assert uc.validate_questions("nope") == ["questions must be a list"]


# --- candidate-facing view -------------------------------------------------


def test_candidate_view_never_leaks_the_answer():
    """The single most important invariant in this module."""
    view = uc.candidate_question_view(_question(), index=0, total=5)
    assert "correct_index" not in view
    assert "rationale" not in view
    # evidence names the file the answer lives in — half the question.
    assert "evidence" not in view
    assert view["options"] == ["_coerce", "_normalize", "_flatten", "_expand"]
    assert view["index"] == 0 and view["total"] == 5


def test_next_question_serves_one_at_a_time_in_order():
    assessment = _assessment([_question("q1"), _question("q2")])
    first = uc.next_question(assessment)
    assert first["id"] == "q1"

    uc.record_answer(assessment, question_id="q1", selected_index=2)
    second = uc.next_question(assessment)
    assert second["id"] == "q2"

    uc.record_answer(assessment, question_id="q2", selected_index=2)
    assert uc.next_question(assessment) is None


# --- answering -------------------------------------------------------------


def test_correct_and_incorrect_answers():
    assessment = _assessment([_question("q1", correct=2)])
    record = uc.record_answer(assessment, question_id="q1", selected_index=2)
    assert record["is_correct"] is True

    assessment = _assessment([_question("q1", correct=2)])
    record = uc.record_answer(assessment, question_id="q1", selected_index=0)
    assert record["is_correct"] is False
    assert record["timed_out"] is False


def test_skipped_answer_is_incorrect_but_distinguishable():
    assessment = _assessment([_question("q1")])
    record = uc.record_answer(assessment, question_id="q1", selected_index=None)
    assert record["is_correct"] is False
    assert record["timed_out"] is True
    assert record["selected_index"] is None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"question_id": "nope", "selected_index": 0}, "unknown_question"),
        ({"question_id": "q1", "selected_index": 9}, "invalid_option"),
    ],
)
def test_bad_answers_raise(kwargs, message):
    assessment = _assessment([_question("q1")])
    with pytest.raises(ValueError, match=message):
        uc.record_answer(assessment, **kwargs)


def test_cannot_answer_the_same_question_twice():
    assessment = _assessment([_question("q1")])
    uc.record_answer(assessment, question_id="q1", selected_index=0)
    with pytest.raises(ValueError, match="already_answered"):
        uc.record_answer(assessment, question_id="q1", selected_index=2)


# --- server-side deadline --------------------------------------------------


def test_mark_served_is_idempotent_so_a_refresh_buys_no_time():
    assessment = _assessment([_question("q1")])
    first = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    later = first + timedelta(minutes=5)

    assert uc.mark_served(assessment, "q1", now=first) == first
    # A re-fetch must return the ORIGINAL stamp, not restart the clock.
    assert uc.mark_served(assessment, "q1", now=later) == first


def test_answer_after_the_server_deadline_cannot_be_correct():
    """The browser timer is candidate-controlled; the server's is not."""
    assessment = _assessment([_question("q1", correct=2)])
    served = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    uc.mark_served(assessment, "q1", now=served)

    late = served + timedelta(seconds=uc.PER_QUESTION_SECONDS + 60)
    record = uc.record_answer(
        assessment,
        question_id="q1",
        selected_index=2,  # the right answer...
        elapsed_ms=1000,  # ...with the client claiming it was fast
        now=late,
    )
    assert record["is_correct"] is False
    assert record["timed_out"] is True
    # The honest measurement is kept alongside the claim.
    assert record["elapsed_ms"] > record["client_elapsed_ms"]


def test_answer_inside_the_grace_period_still_counts():
    assessment = _assessment([_question("q1", correct=2)])
    served = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    uc.mark_served(assessment, "q1", now=served)
    on_time = served + timedelta(seconds=uc.PER_QUESTION_SECONDS + 5)
    record = uc.record_answer(
        assessment, question_id="q1", selected_index=2, now=on_time
    )
    assert record["is_correct"] is True


# --- scoring ---------------------------------------------------------------


def test_score_denominator_is_questions_asked_not_answered():
    """Stopping early must not round a candidate up to 100%."""
    assessment = _assessment([_question(f"q{i}", correct=1) for i in range(1, 5)])
    uc.record_answer(assessment, question_id="q1", selected_index=1)
    uc.record_answer(assessment, question_id="q2", selected_index=1)
    # Two of FOUR asked, not two of two answered.
    assert uc.score_answers(assessment) == 50.0


def test_score_is_none_when_nothing_was_ever_asked():
    assert uc.score_answers(_assessment(None)) is None
    assert uc.score_answers(_assessment([])) is None


# --- window lifecycle ------------------------------------------------------


def test_reserve_window_opens_without_questions_and_costs_no_model_call():
    """Submit must stay fast; generation happens on the first check fetch."""
    assessment = _assessment()
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    uc.reserve_window(assessment, now=now)
    assert assessment.understanding_check_status == uc.STATUS_GENERATING
    assert assessment.understanding_check_questions == []
    # A window awaiting generation still holds grading back.
    assert uc.is_window_open(assessment, now=now) is True


def test_a_reserved_window_expires_on_the_submit_clock_not_the_generation_clock():
    """Otherwise a candidate who never opens the check parks grading forever."""
    assessment = _assessment()
    submitted = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    uc.reserve_window(assessment, now=submitted)

    # Questions arrive ten minutes later; the expiry must not move.
    generated = submitted + timedelta(minutes=10)
    uc.open_window(assessment, [_question()], now=generated)
    assert assessment.understanding_check_expires_at == submitted + timedelta(
        minutes=uc.WINDOW_MINUTES
    )
    assert assessment.understanding_check_started_at == submitted


def test_open_window_sets_pending_with_a_bounded_expiry():
    assessment = _assessment()
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    uc.open_window(assessment, [_question()], now=now)
    assert assessment.understanding_check_status == uc.STATUS_PENDING
    assert assessment.understanding_check_expires_at == now + timedelta(
        minutes=uc.WINDOW_MINUTES
    )
    assert uc.is_window_open(assessment, now=now) is True
    assert uc.is_window_open(
        assessment, now=now + timedelta(minutes=uc.WINDOW_MINUTES + 1)
    ) is False


def test_window_with_no_expiry_reads_as_closed():
    """Grading waits on this predicate, so it must fail closed."""
    assessment = _assessment([_question()])
    assessment.understanding_check_status = uc.STATUS_PENDING
    assessment.understanding_check_expires_at = None
    assert uc.is_window_open(assessment) is False


def test_close_window_is_idempotent_and_keeps_the_first_status():
    """A late expiry sweep must not rewrite a completed run as expired."""
    assessment = _assessment([_question("q1", correct=1)])
    uc.open_window(assessment, [_question("q1", correct=1)])
    uc.record_answer(assessment, question_id="q1", selected_index=1)

    assert uc.close_window(assessment, status=uc.STATUS_COMPLETED) == 100.0
    uc.close_window(assessment, status=uc.STATUS_EXPIRED)
    assert assessment.understanding_check_status == uc.STATUS_COMPLETED
    assert assessment.understanding_check_score == 100.0


def test_close_window_rejects_a_non_terminal_status():
    assessment = _assessment([_question()])
    uc.open_window(assessment, [_question()])
    with pytest.raises(ValueError):
        uc.close_window(assessment, status=uc.STATUS_PENDING)


def test_skip_window_records_not_assessed_rather_than_zero():
    assessment = _assessment()
    uc.skip_window(assessment, reason="generator_failed")
    assert assessment.understanding_check_status == uc.STATUS_SKIPPED
    assert assessment.understanding_check_score is None
    assert uc.is_window_open(assessment) is False


def test_summarize_withholds_detail_until_the_check_is_closed():
    assessment = _assessment()
    uc.open_window(assessment, [_question("q1"), _question("q2")])
    uc.record_answer(assessment, question_id="q1", selected_index=2)

    open_summary = uc.summarize(assessment)
    assert open_summary["status"] == uc.STATUS_PENDING
    assert open_summary["questions"] == []

    uc.record_answer(assessment, question_id="q2", selected_index=0)
    uc.close_window(assessment, status=uc.STATUS_COMPLETED)
    closed = uc.summarize(assessment)
    assert len(closed["questions"]) == 2
    assert closed["questions_correct"] == 1
    assert closed["score"] == 50.0


def test_summarize_counts_tab_switches_across_the_check():
    assessment = _assessment()
    uc.open_window(assessment, [_question("q1"), _question("q2")])
    uc.record_answer(assessment, question_id="q1", selected_index=2, tab_switches=2)
    uc.record_answer(assessment, question_id="q2", selected_index=2, tab_switches=3)
    assert uc.summarize(assessment)["tab_switches_during_check"] == 5


# --- the comprehension_outcome grader --------------------------------------


def _scorer() -> RubricScorer:
    return RubricScorer(api_key="test-key", organization_id=1)


def _graded(summary: Dict[str, Any]):
    return _scorer().grade_dimension_via_comprehension_outcome(
        "submission_comprehension",
        ScoringArtifacts(understanding_check=summary),
        weight=0.07,
    )


def _summary_for(correct: int, total: int, status: str = "completed") -> Dict[str, Any]:
    assessment = _assessment()
    uc.open_window(assessment, [_question(f"q{i}", correct=1) for i in range(total)])
    for i in range(total):
        uc.record_answer(
            assessment, question_id=f"q{i}", selected_index=1 if i < correct else 0
        )
    uc.close_window(assessment, status=status)
    return uc.summarize(assessment)


@pytest.mark.parametrize(
    "correct, total, score, rating",
    [
        (5, 5, 10.0, "excellent"),
        (4, 5, 8.0, "excellent"),
        (3, 5, 6.0, "good"),
        (2, 5, 4.0, "poor"),
        (0, 5, 0.0, "poor"),
    ],
)
def test_grader_buckets(correct, total, score, rating):
    grade = _graded(_summary_for(correct, total))
    assert grade.score == score
    assert grade.rating == rating
    assert grade.error is None


def test_grader_marks_a_missing_check_not_assessed_rather_than_zero():
    """A run from before this feature must not be re-rated downward."""
    for summary in ({}, {"status": "skipped", "questions_total": 0}):
        grade = _graded(summary)
        assert grade.error == "understanding_check_not_asked"


def test_grader_still_grades_an_expired_check_on_what_was_asked():
    """Abandoning the check is a result, not an absence."""
    assessment = _assessment()
    uc.open_window(assessment, [_question(f"q{i}", correct=1) for i in range(4)])
    uc.record_answer(assessment, question_id="q0", selected_index=1)
    uc.close_window(assessment, status=uc.STATUS_EXPIRED)

    grade = _graded(uc.summarize(assessment))
    assert grade.error is None
    assert grade.score == 2.5  # 1 of 4
    assert "expired" in grade.reasoning


def test_grader_defers_on_a_still_open_check():
    assessment = _assessment()
    uc.open_window(assessment, [_question()])
    grade = _graded(uc.summarize(assessment))
    assert grade.error == "understanding_check_incomplete"


def test_grade_rubric_dispatches_on_the_grader_field():
    result = _scorer().grade_rubric(
        {"submission_comprehension": {"weight": 1.0, "grader": "comprehension_outcome"}},
        ScoringArtifacts(understanding_check=_summary_for(4, 5)),
    )
    assert [d.dimension_id for d in result.dimensions] == ["submission_comprehension"]
    assert result.dimensions[0].score == 8.0


def test_comprehension_dimension_rolls_up_to_discernment():
    from app.components.assessments.fluency_axes import fluency_axis_for_dimension

    axis = fluency_axis_for_dimension({"grader": "comprehension_outcome"})
    assert axis == "discernment"
