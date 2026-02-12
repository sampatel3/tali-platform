"""Comprehensive scoring engine unit tests — 30+ metrics across 8 categories."""
import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from app.components.scoring.service import (
    CATEGORY_WEIGHTS,
    _score_task_completion,
    _score_prompt_clarity,
    _score_context_provision,
    _score_independence,
    _score_utilization,
    _score_communication,
    _score_approach,
    _score_cv_match,
    _detect_fraud,
    _compute_per_prompt_scores,
    calculate_mvp_score,
)
from app.components.scoring.rules import FRAUD_SCORE_CAP


def _make_interactions(messages, **extras):
    """Build a list of user/assistant interaction pairs."""
    result = []
    for i, msg in enumerate(messages):
        entry = {"role": "user", "message": msg, "content": msg}
        entry.update(extras)
        result.append(entry)
        result.append({"role": "assistant", "message": f"Response to: {msg}", "content": f"Response to: {msg}"})
    return result


# ===================================================================
# CATEGORY WEIGHTS
# ===================================================================

class TestCategoryWeights:
    def test_weights_sum_to_one(self):
        total = sum(CATEGORY_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_all_eight_categories_present(self):
        expected = {"task_completion", "prompt_clarity", "context_provision",
                    "independence", "utilization", "communication", "approach", "cv_match"}
        assert set(CATEGORY_WEIGHTS.keys()) == expected

    def test_all_weights_positive(self):
        for cat, w in CATEGORY_WEIGHTS.items():
            assert w > 0, f"Weight for {cat} is {w}, must be positive"


# ===================================================================
# TASK COMPLETION
# ===================================================================

class TestScoreTaskCompletion:
    def test_perfect_score(self):
        result = _score_task_completion(10, 10, 25.0, 30)
        assert result["score"] >= 8.0, f"Perfect test pass should score high, got {result['score']}"
        assert "explanation" in result or "explanations" in result

    def test_zero_tests_passed(self):
        result = _score_task_completion(0, 10, 25.0, 30)
        assert result["score"] <= 7.0  # Low test pass rate, but on-time

    def test_no_tests_at_all(self):
        result = _score_task_completion(0, 0, 25.0, 30)
        assert 0 <= result["score"] <= 10

    def test_time_over_limit(self):
        result = _score_task_completion(10, 10, 60.0, 30)
        # Went 2x over time limit — time compliance should be penalized
        perfect_result = _score_task_completion(10, 10, 25.0, 30)
        assert result["score"] <= perfect_result["score"]


# ===================================================================
# PROMPT CLARITY
# ===================================================================

class TestScorePromptClarity:
    def test_well_written_prompts(self):
        prompts = [
            {"role": "user", "message": "Can you help me understand how to implement a binary search algorithm? I need to search through a sorted list of integers and return the index of the target value. What approach would you recommend?"},
            {"role": "user", "message": "I'm getting an index out of range error when the target is not in the list. How should I handle the case where the element is not found in the array?"},
        ]
        result = _score_prompt_clarity(prompts)
        assert result["score"] >= 3.0, f"Clear prompts should score reasonably, got {result['score']}"

    def test_vague_prompts(self):
        prompts = [
            {"role": "user", "message": "help"},
            {"role": "user", "message": "fix it"},
            {"role": "user", "message": "not working"},
        ]
        result = _score_prompt_clarity(prompts)
        # Vague prompts should score lower than clear ones
        clear_prompts = [
            {"role": "user", "message": "Can you help me fix the sorting algorithm? The quicksort function is not handling duplicate elements correctly."},
        ]
        clear_result = _score_prompt_clarity(clear_prompts)
        assert result["score"] <= clear_result["score"]

    def test_empty_interactions(self):
        result = _score_prompt_clarity([])
        assert 0 <= result["score"] <= 10


# ===================================================================
# CONTEXT PROVISION
# ===================================================================

class TestScoreContextProvision:
    def test_prompts_with_code_context(self):
        prompts = [
            {"role": "user", "message": "Here is my code", "code_snippet_included": True, "error_message_included": True},
        ]
        result = _score_context_provision(prompts)
        assert result["score"] >= 2.0

    def test_prompts_without_context(self):
        prompts = [
            {"role": "user", "message": "It doesn't work"},
        ]
        result = _score_context_provision(prompts)
        # No context flags set — should be low
        assert result["score"] <= 5.0

    def test_empty_prompts(self):
        result = _score_context_provision([])
        assert 0 <= result["score"] <= 10


# ===================================================================
# INDEPENDENCE
# ===================================================================

class TestScoreIndependence:
    def test_few_prompts(self):
        prompts = [
            {"role": "user", "message": "Question about sorting", "time_since_assessment_start_ms": 300000},
            {"role": "user", "message": "Follow up question", "time_since_assessment_start_ms": 900000},
        ]
        result = _score_independence(prompts, tests_passed=5, total_tokens=500)
        assert 0 <= result["score"] <= 10

    def test_many_prompts(self):
        prompts = [{"role": "user", "message": f"Question {i}", "time_since_assessment_start_ms": i * 10000} for i in range(50)]
        result = _score_independence(prompts, tests_passed=0, total_tokens=10000)
        assert 0 <= result["score"] <= 10

    def test_empty_prompts(self):
        result = _score_independence([], tests_passed=0, total_tokens=0)
        assert 0 <= result["score"] <= 10


# ===================================================================
# UTILIZATION
# ===================================================================

class TestScoreUtilization:
    def test_normal_utilization(self):
        prompts = _make_interactions(["How do I sort a list?", "Thanks, that works"])
        result = _score_utilization(prompts)
        assert 0 <= result["score"] <= 10

    def test_empty(self):
        result = _score_utilization([])
        assert 0 <= result["score"] <= 10


# ===================================================================
# COMMUNICATION
# ===================================================================

class TestScoreCommunication:
    def test_professional_writing(self):
        prompts = [
            {"role": "user", "message": "Could you please help me implement a proper error handling mechanism for the API endpoints? I would like to ensure all exceptions are caught and meaningful error messages are returned."},
        ]
        result = _score_communication(prompts)
        assert 0 <= result["score"] <= 10

    def test_unprofessional_writing(self):
        prompts = [
            {"role": "user", "message": "wtf this doesnt work lol fix it bruh"},
        ]
        result = _score_communication(prompts)
        professional = [
            {"role": "user", "message": "This function is not returning the expected output. Could you help me identify the issue with the comparison logic?"},
        ]
        prof_result = _score_communication(professional)
        assert result["score"] <= prof_result["score"]

    def test_empty(self):
        result = _score_communication([])
        assert 0 <= result["score"] <= 10


# ===================================================================
# APPROACH
# ===================================================================

class TestScoreApproach:
    def test_with_debugging_patterns(self):
        prompts = [
            {"role": "user", "message": "I suspect the error is in the loop. Let me add a print statement to debug step by step."},
        ]
        result = _score_approach(prompts)
        assert 0 <= result["score"] <= 10

    def test_empty(self):
        result = _score_approach([])
        assert 0 <= result["score"] <= 10


# ===================================================================
# CV MATCH
# ===================================================================

class TestScoreCvMatch:
    def test_good_match(self):
        cv_match = {"cv_job_match_score": 85, "skills_match": 90, "experience_relevance": 80}
        result = _score_cv_match(cv_match)
        assert result["score"] >= 5.0, f"Good CV match should score well, got {result['score']}"

    def test_zero_match(self):
        cv_match = {"cv_job_match_score": 0, "skills_match": 0, "experience_relevance": 0}
        result = _score_cv_match(cv_match)
        assert result["score"] <= 3.0

    def test_none_input(self):
        result = _score_cv_match(None)
        score = result["score"]
        # When no CV match data, score might be None or 0
        assert score is None or (0 <= score <= 10)


# ===================================================================
# FRAUD DETECTION
# ===================================================================

class TestDetectFraud:
    def test_clean_session(self):
        prompts = [
            {"role": "user", "message": "Normal question about sorting", "paste_detected": False, "paste_length": 0, "time_since_assessment_start_ms": 120000},
        ]
        result = _detect_fraud(prompts, total_duration_seconds=1800, tests_passed=5)
        flags = result.get("flags", [])
        assert isinstance(flags, list)

    def test_high_paste_events(self):
        prompts = [
            {"role": "user", "message": "x" * 500, "paste_detected": True, "paste_length": 500, "time_since_assessment_start_ms": i * 30000}
            for i in range(10)
        ]
        result = _detect_fraud(prompts, total_duration_seconds=1800, tests_passed=5)
        flags = result.get("flags", [])
        assert len(flags) > 0, "Heavy pasting should trigger fraud flags"

    def test_suspiciously_fast(self):
        prompts = [
            {"role": "user", "message": "question", "paste_detected": False, "time_since_assessment_start_ms": i * 10000}
            for i in range(5)
        ]
        result = _detect_fraud(prompts, total_duration_seconds=120, tests_passed=8)
        flags = result.get("flags", [])
        # Completing with many tests passed in <5 min is suspicious
        fast_flag = any("fast" in str(f).lower() or "suspicious" in str(f).lower() for f in flags)
        assert fast_flag, "Suspiciously fast session should be flagged"


# ===================================================================
# PER-PROMPT SCORES
# ===================================================================

class TestComputePerPromptScores:
    def test_normal_prompts(self):
        prompts = [
            {"role": "user", "message": "How do I implement binary search in Python?"},
            {"role": "user", "message": "fix"},
        ]
        result = _compute_per_prompt_scores(prompts)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_empty_list(self):
        result = _compute_per_prompt_scores([])
        assert isinstance(result, list)
        assert len(result) == 0


# ===================================================================
# FULL MVP SCORE
# ===================================================================

class TestCalculateMvpScore:
    def test_full_calculation_returns_all_fields(self):
        interactions = _make_interactions(["How do I sort?", "Thanks"])
        result = calculate_mvp_score(
            interactions=interactions,
            tests_passed=8, tests_total=10,
            total_duration_seconds=1800, time_limit_minutes=30,
        )
        assert "final_score" in result
        assert "category_scores" in result
        assert "fraud" in result
        assert len(result["category_scores"]) == 8

    def test_score_in_0_100_range(self):
        interactions = _make_interactions(["Question 1", "Question 2"])
        result = calculate_mvp_score(
            interactions=interactions,
            tests_passed=5, tests_total=10,
            total_duration_seconds=1200, time_limit_minutes=30,
        )
        assert 0 <= result["final_score"] <= 100

    def test_fraud_flags_cap_score(self):
        # Many pastes + suspiciously fast should cap the score
        prompts = [
            {"role": "user", "message": "x" * 500, "paste_detected": True, "paste_length": 500, "time_since_assessment_start_ms": i * 5000}
            for i in range(10)
        ]
        result = calculate_mvp_score(
            interactions=prompts,
            tests_passed=10, tests_total=10,
            total_duration_seconds=120, time_limit_minutes=30,
        )
        if result["fraud"]["flags"]:
            assert result["final_score"] <= FRAUD_SCORE_CAP + 5  # small tolerance

    def test_custom_weights_override(self):
        interactions = _make_interactions(["test"])
        custom = {"task_completion": 1.0, "prompt_clarity": 0, "context_provision": 0,
                  "independence": 0, "utilization": 0, "communication": 0, "approach": 0, "cv_match": 0}
        result = calculate_mvp_score(
            interactions=interactions,
            tests_passed=10, tests_total=10,
            total_duration_seconds=1800, time_limit_minutes=30,
            weights=custom,
        )
        assert "weights_used" in result

    def test_zero_interactions(self):
        result = calculate_mvp_score(
            interactions=[],
            tests_passed=0, tests_total=0,
            total_duration_seconds=0, time_limit_minutes=30,
        )
        assert 0 <= result["final_score"] <= 100

    def test_legacy_component_scores_present(self):
        interactions = _make_interactions(["test question"])
        result = calculate_mvp_score(
            interactions=interactions,
            tests_passed=5, tests_total=10,
            total_duration_seconds=1800, time_limit_minutes=30,
        )
        assert "component_scores" in result
        assert isinstance(result["component_scores"], dict)

    def test_cv_match_included(self):
        interactions = _make_interactions(["question"])
        cv_match = {"cv_job_match_score": 85, "skills_match": 90, "experience_relevance": 80}
        result = calculate_mvp_score(
            interactions=interactions,
            tests_passed=5, tests_total=10,
            total_duration_seconds=1800, time_limit_minutes=30,
            cv_match_result=cv_match,
        )
        assert "cv_match" in result["category_scores"]
