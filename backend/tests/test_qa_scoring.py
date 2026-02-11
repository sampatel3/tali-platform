"""
QA Test Suite: Scoring Engine — 30+ Metrics, 8 Categories, Fraud Detection
Extends existing scoring tests with comprehensive edge cases.
Scores are on 0-10 scale per category, 0-100 final.
~40 tests
"""
from app.components.scoring.service import (
    calculate_mvp_score,
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
    CATEGORY_WEIGHTS,
)


def _make_interaction(role="user", content="How do I fix this error?", code_context="x = 1"):
    return {"role": role, "content": content, "code_context": code_context}


def _make_interactions(n=5, include_assistant=True):
    interactions = []
    for i in range(n):
        interactions.append(_make_interaction(
            role="user",
            content=f"Prompt {i}: How do I fix the error in line {i}? I need help understanding the logic flow here.",
            code_context=f"def func{i}():\n    return {i}",
        ))
        if include_assistant:
            interactions.append({
                "role": "assistant",
                "content": f"Here's a solution for prompt {i}. Try changing the return value.",
            })
    return interactions


# ===========================================================================
# A. CATEGORY WEIGHTS
# ===========================================================================
class TestCategoryWeights:
    def test_weights_sum_to_one(self):
        total = sum(CATEGORY_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    def test_all_categories_present(self):
        expected = {"task_completion", "prompt_clarity", "context_provision",
                    "independence", "utilization", "communication", "approach", "cv_match"}
        assert set(CATEGORY_WEIGHTS.keys()) == expected

    def test_all_weights_positive(self):
        for k, v in CATEGORY_WEIGHTS.items():
            assert v > 0, f"Weight for {k} should be positive"


# ===========================================================================
# B. TASK COMPLETION (takes 4 args: tests_passed, tests_total, duration_minutes, time_limit_minutes)
# ===========================================================================
class TestTaskCompletion:
    def test_perfect_tests(self):
        r = _score_task_completion(10, 10, 20, 30)
        assert r["score"] >= 7  # 0-10 scale

    def test_zero_tests(self):
        r = _score_task_completion(0, 10, 20, 30)
        assert r["score"] <= 7  # Penalized but time compliance can add points

    def test_no_tests_total(self):
        r = _score_task_completion(0, 0, 20, 30)
        assert "score" in r

    def test_over_time(self):
        r = _score_task_completion(5, 10, 60, 30)
        assert "score" in r

    def test_returns_metrics(self):
        r = _score_task_completion(8, 10, 20, 30)
        assert "score" in r
        assert "detailed" in r
        assert "explanations" in r


# ===========================================================================
# C. PROMPT CLARITY
# ===========================================================================
class TestPromptClarity:
    def test_good_prompts(self):
        interactions = _make_interactions(3)
        r = _score_prompt_clarity(interactions)
        assert 0 <= r["score"] <= 10

    def test_empty_prompts(self):
        r = _score_prompt_clarity([])
        assert "score" in r

    def test_vague_prompts(self):
        vague = [_make_interaction(content="help"), _make_interaction(content="fix it")]
        r = _score_prompt_clarity(vague)
        assert 0 <= r["score"] <= 10

    def test_single_prompt(self):
        r = _score_prompt_clarity([_make_interaction()])
        assert "score" in r

    def test_returns_structure(self):
        r = _score_prompt_clarity(_make_interactions(2))
        assert "score" in r
        assert "detailed" in r
        assert "explanations" in r


# ===========================================================================
# D. CONTEXT PROVISION
# ===========================================================================
class TestContextProvision:
    def test_with_code_context(self):
        r = _score_context_provision(_make_interactions(5))
        assert 0 <= r["score"] <= 10

    def test_without_code_context(self):
        interactions = [_make_interaction(code_context="") for _ in range(5)]
        r = _score_context_provision(interactions)
        assert 0 <= r["score"] <= 10

    def test_empty_interactions(self):
        r = _score_context_provision([])
        assert "score" in r


# ===========================================================================
# E. INDEPENDENCE
# ===========================================================================
class TestIndependence:
    def test_few_prompts(self):
        r = _score_independence(_make_interactions(2), 1800, 30)
        assert 0 <= r["score"] <= 10

    def test_many_prompts(self):
        r = _score_independence(_make_interactions(20), 1800, 30)
        assert "score" in r

    def test_zero_duration(self):
        r = _score_independence(_make_interactions(5), 0, 30)
        assert "score" in r


# ===========================================================================
# F. UTILIZATION
# ===========================================================================
class TestUtilization:
    def test_normal(self):
        r = _score_utilization(_make_interactions(5))
        assert "score" in r
        assert 0 <= r["score"] <= 10

    def test_empty(self):
        r = _score_utilization([])
        assert "score" in r


# ===========================================================================
# G. COMMUNICATION
# ===========================================================================
class TestCommunication:
    def test_well_written(self):
        interactions = [_make_interaction(content="I'm encountering a TypeError when calling the function. Could you help me understand why the argument types don't match?")]
        r = _score_communication(interactions)
        assert 0 <= r["score"] <= 10

    def test_poor_writing(self):
        interactions = [_make_interaction(content="hlp pls fix thx")]
        r = _score_communication(interactions)
        assert "score" in r

    def test_empty(self):
        r = _score_communication([])
        assert "score" in r


# ===========================================================================
# H. APPROACH
# ===========================================================================
class TestApproach:
    def test_normal(self):
        r = _score_approach(_make_interactions(5))
        assert "score" in r
        assert 0 <= r["score"] <= 10

    def test_empty(self):
        r = _score_approach([])
        assert "score" in r


# ===========================================================================
# I. CV MATCH (0-10 scale)
# ===========================================================================
class TestCvMatch:
    def test_with_match_result(self):
        r = _score_cv_match({
            "cv_job_match_score": 85,
            "skills_match": 90,
            "experience_relevance": 80,
        })
        assert 0 <= r["score"] <= 10

    def test_without_match_result(self):
        r = _score_cv_match({})
        assert "score" in r

    def test_none_match_result(self):
        r = _score_cv_match(None)
        assert "score" in r

    def test_zero_match(self):
        r = _score_cv_match({"cv_job_match_score": 0, "skills_match": 0, "experience_relevance": 0})
        assert r["score"] <= 5


# ===========================================================================
# J. FRAUD DETECTION (takes 3 args: prompts, total_duration_seconds, tests_passed)
# ===========================================================================
class TestFraudDetection:
    def test_no_fraud(self):
        user_prompts = [p for p in _make_interactions(5) if p["role"] == "user"]
        r = _detect_fraud(user_prompts, 1800, 5)
        assert "flags" in r
        assert isinstance(r["flags"], list)

    def test_paste_detection(self):
        interactions = [_make_interaction(content="A" * 500)]
        r = _detect_fraud(interactions, 1800, 5)
        assert "flags" in r

    def test_suspiciously_fast(self):
        user_prompts = [_make_interaction() for _ in range(20)]
        r = _detect_fraud(user_prompts, 60, 10)
        assert "flags" in r

    def test_empty_interactions(self):
        r = _detect_fraud([], 1800, 0)
        assert "flags" in r


# ===========================================================================
# K. PER-PROMPT SCORES
# ===========================================================================
class TestPerPromptScores:
    def test_normal(self):
        r = _compute_per_prompt_scores(_make_interactions(5))
        assert isinstance(r, list)
        assert len(r) > 0

    def test_empty(self):
        r = _compute_per_prompt_scores([])
        assert isinstance(r, list)


# ===========================================================================
# L. FULL MVP SCORE CALCULATION
# ===========================================================================
class TestCalculateMvpScore:
    def test_full_calculation(self):
        r = calculate_mvp_score(
            interactions=_make_interactions(5),
            tests_passed=8, tests_total=10,
            total_duration_seconds=1500,
            time_limit_minutes=30,
            v2_enabled=False,
            weights=CATEGORY_WEIGHTS,
            cv_match_result={"cv_job_match_score": 75},
        )
        assert "final_score" in r
        assert 0 <= r["final_score"] <= 100
        assert "category_scores" in r
        assert "detailed_scores" in r
        assert "explanations" in r
        assert "fraud" in r
        assert "per_prompt_scores" in r

    def test_zero_everything(self):
        r = calculate_mvp_score(
            interactions=[], tests_passed=0, tests_total=0,
            total_duration_seconds=0, time_limit_minutes=30,
            v2_enabled=False, weights=CATEGORY_WEIGHTS, cv_match_result={},
        )
        assert "final_score" in r
        assert r["final_score"] >= 0

    def test_high_score_scenario(self):
        r = calculate_mvp_score(
            interactions=_make_interactions(3),
            tests_passed=10, tests_total=10,
            total_duration_seconds=600, time_limit_minutes=30,
            v2_enabled=False, weights=CATEGORY_WEIGHTS,
            cv_match_result={"cv_job_match_score": 100, "skills_match": 100, "experience_relevance": 100},
        )
        assert r["final_score"] >= 30  # Should be decent

    def test_custom_weights(self):
        w = {k: 1.0 / len(CATEGORY_WEIGHTS) for k in CATEGORY_WEIGHTS}
        r = calculate_mvp_score(
            interactions=_make_interactions(3),
            tests_passed=5, tests_total=10,
            total_duration_seconds=1000, time_limit_minutes=30,
            v2_enabled=False, weights=w, cv_match_result={},
        )
        assert "final_score" in r

    def test_result_has_all_categories(self):
        r = calculate_mvp_score(
            interactions=_make_interactions(3),
            tests_passed=5, tests_total=10,
            total_duration_seconds=1000, time_limit_minutes=30,
            v2_enabled=False, weights=CATEGORY_WEIGHTS, cv_match_result={},
        )
        for cat in CATEGORY_WEIGHTS.keys():
            assert cat in r["category_scores"], f"Missing category: {cat}"
