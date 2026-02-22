"""MVP scoring orchestration facade.

This module keeps the stable public API while core scoring heuristics live in
`scoring_core.py`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .rules import FRAUD_SCORE_CAP, INJECTION_PATTERNS, VAGUE_PATTERNS
from .scoring_core import (
    CATEGORY_WEIGHTS,
    SEVERE_LANGUAGE_FINAL_SCORE_CAP,
    _bool_rate,
    _build_legacy_component_scores,
    _clamp,
    _compute_per_prompt_scores,
    _count_words,
    _detect_fraud,
    _extract_prompt_metadata,
    _is_vague_prompt,
    _is_solution_dump,
    _question_presence_rate,
    _score_approach,
    _score_communication,
    _score_context_provision,
    _score_cv_match,
    _score_independence,
    _score_prompt_evolution,
    _score_prompt_clarity,
    _score_task_completion,
    _score_utilization,
)


def calculate_mvp_score(
    interactions: List[Dict[str, Any]],
    tests_passed: int,
    tests_total: int,
    total_duration_seconds: int,
    time_limit_minutes: int,
    v2_enabled: bool = False,
    weights: Dict[str, float] | None = None,
    cv_match_result: Dict[str, Any] | None = None,
    task_scoring_hints: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Calculate comprehensive MVP score with 30+ metrics in 8 categories.

    Returns a dict with:
    - final_score (0-100)
    - category_scores (dict of 8 categories, each 0-10)
    - detailed_scores (nested dict with individual metrics per category)
    - explanations (nested dict with human-readable explanations)
    - per_prompt_scores (list of per-prompt breakdowns)
    - fraud (flags and details)
    - component_scores (legacy 12-metric dict for backward compat)
    - weights_used, soft_signals, metric_details
    """
    if v2_enabled:
        raise RuntimeError(
            "SCORING_V2_ENABLED is set, but no production scoring v2 integration is configured."
        )

    prompts = []
    for raw_prompt in interactions or []:
        prompt = dict(raw_prompt or {})
        inferred = _extract_prompt_metadata(prompt.get("message", ""))
        for key, value in inferred.items():
            prompt.setdefault(key, value)
        prompts.append(prompt)
    total_prompts = len(prompts)
    duration_minutes = total_duration_seconds / 60.0 if total_duration_seconds else 0.0

    total_tokens = sum(
        (p.get("input_tokens") or 0) + (p.get("output_tokens") or 0) for p in prompts
    )

    min_reading_time_seconds = None
    if isinstance(task_scoring_hints, dict):
        raw_min_reading = task_scoring_hints.get("min_reading_time_seconds")
        try:
            if raw_min_reading is not None:
                min_reading_time_seconds = max(0, int(raw_min_reading))
        except (TypeError, ValueError):
            min_reading_time_seconds = None

    # --- Score all 8 categories ---
    cat_results: Dict[str, Dict] = {}
    cat_results["task_completion"] = _score_task_completion(
        tests_passed,
        tests_total,
        duration_minutes,
        time_limit_minutes,
    )
    cat_results["prompt_clarity"] = _score_prompt_clarity(prompts)
    cat_results["context_provision"] = _score_context_provision(prompts)
    cat_results["independence"] = _score_independence(
        prompts,
        tests_passed,
        total_tokens,
        min_reading_time_seconds=min_reading_time_seconds,
    )
    cat_results["utilization"] = _score_utilization(prompts)
    cat_results["communication"] = _score_communication(prompts)
    cat_results["approach"] = _score_approach(prompts)
    cat_results["cv_match"] = _score_cv_match(cv_match_result)

    # --- Category scores (0-10) ---
    category_scores = {}
    for cat_key, result in cat_results.items():
        category_scores[cat_key] = result["score"]

    # --- Detailed metric scores ---
    detailed_scores = {}
    for cat_key, result in cat_results.items():
        detailed_scores[cat_key] = result["detailed"]

    # --- Explanations ---
    explanations = {}
    for cat_key, result in cat_results.items():
        explanations[cat_key] = result["explanations"]

    # --- Weighted final score (0-100) ---
    used_weights = {**CATEGORY_WEIGHTS, **(weights or {})}
    weighted_sum = 0.0
    total_weight = 0.0
    for cat_key, w in used_weights.items():
        score = category_scores.get(cat_key)
        if score is not None:
            weighted_sum += score * w * 10.0  # Convert 0-10 to 0-100 contribution
            total_weight += w
    # Excluding None-scored categories (for example cv_match when no CV/job spec is
    # available) redistributes remaining weights across the denominator.
    final_score = round(_clamp(weighted_sum / total_weight if total_weight > 0 else 0.0), 2)
    uncapped_final_score = final_score
    applied_caps: list[str] = []
    communication_flags = (cat_results.get("communication", {}) or {}).get("flags", {}) or {}
    severe_unprofessional_language = bool(communication_flags.get("severe_unprofessional_language"))
    if severe_unprofessional_language:
        applied_caps.append("severe_unprofessional_language")
        final_score = round(min(final_score, SEVERE_LANGUAGE_FINAL_SCORE_CAP), 2)

    # --- Fraud detection ---
    fraud = _detect_fraud(prompts, total_duration_seconds, tests_passed)
    if severe_unprofessional_language:
        fraud_flags = list(fraud.get("flags") or [])
        if "severe_unprofessional_language" not in fraud_flags:
            fraud_flags.append("severe_unprofessional_language")
        fraud["flags"] = fraud_flags
    if fraud["flags"]:
        applied_caps.append("fraud")
        final_score = round(min(final_score, FRAUD_SCORE_CAP), 2)

    # --- Per-prompt scores ---
    per_prompt_scores = _compute_per_prompt_scores(prompts)

    # --- Legacy component scores (backward compat) ---
    component_scores = _build_legacy_component_scores(
        cat_results,
        prompts,
        tests_passed,
        tests_total,
        total_tokens,
        duration_minutes,
        time_limit_minutes,
    )

    # --- Soft signals ---
    first_sec = ((prompts[0].get("time_since_assessment_start_ms") or 0) / 1000.0) if prompts else 0
    gaps = [
        (p.get("time_since_last_prompt_ms") or 0) / 1000.0
        for p in prompts[1:]
        if p.get("time_since_last_prompt_ms")
    ]
    avg_gap = (sum(gaps) / len(gaps)) if gaps else 0
    word_counts = [_count_words(p.get("message", "")) for p in prompts]

    prompt_evolution = _score_prompt_evolution(prompts)

    soft_signals = {
        "prompt_count_total": total_prompts,
        "session_duration_minutes": round(duration_minutes, 2),
        "test_runs_count": tests_total,
        "first_test_run_timestamp": None,
        "prompt_timestamps": [p.get("timestamp") for p in prompts],
        "peak_prompting_period": "early"
        if total_prompts and total_prompts // 2 >= (total_prompts - total_prompts // 2)
        else "late",
        "tests_passed_count": tests_passed,
        "tests_total": tests_total,
        "completion_time_minutes": round(duration_minutes, 2),
        "time_to_first_prompt_sec": round(first_sec, 2),
        "avg_time_between_prompts_sec": round(avg_gap, 2),
        "prompts_per_test_passed": round(total_prompts / max(tests_passed, 1), 3)
        if tests_passed
        else None,
        "tokens_per_test_passed": round(total_tokens / max(tests_passed, 1), 3)
        if tests_passed
        else None,
        "total_prompts": total_prompts,
        "total_tokens_used": total_tokens,
        "prompt_evolution": prompt_evolution,
    }

    # --- Backward-compatible metric_details ---
    first_half = prompts[: max(1, total_prompts // 2)]
    second_half = prompts[max(1, total_prompts // 2) :] if total_prompts > 1 else []
    first_quality = (
        sum(_count_words(p.get("message", "")) for p in first_half) / len(first_half)
        if first_half
        else 0.0
    )
    second_quality = (
        sum(_count_words(p.get("message", "")) for p in second_half) / len(second_half)
        if second_half
        else first_quality
    )
    prompt_quality_trend = 1.0 if second_quality >= first_quality else 0.0

    metric_details = {
        "word_count_avg": round(sum(word_counts) / len(word_counts) if word_counts else 0, 2),
        "word_count_min": min(word_counts) if word_counts else 0,
        "word_count_max": max(word_counts) if word_counts else 0,
        "question_presence": round(_question_presence_rate(prompts), 3),
        "code_snippet_rate": round(_bool_rate(prompts, "code_snippet_included"), 3),
        "error_message_rate": round(_bool_rate(prompts, "error_message_included"), 3),
        "line_reference_rate": round(_bool_rate(prompts, "line_number_referenced"), 3),
        "file_reference_rate": round(_bool_rate(prompts, "file_reference"), 3),
        "vague_prompt_count": sum(
            1
            for p in prompts
            if _is_vague_prompt(p.get("message", ""))
        ),
        "specific_prompt_count": total_prompts
        - sum(
            1
            for p in prompts
            if _is_vague_prompt(p.get("message", ""))
        ),
        "early_prompt_penalty": 25.0 if first_sec < 60 else 0.0,
        "code_changes_before_prompts": sum(
            1
            for p in prompts
            if (p.get("code_before", "") or "") != (p.get("code_after", "") or "")
        ),
        "prompt_quality_trend": prompt_quality_trend,
        "builds_on_previous": round(_bool_rate(prompts, "references_previous"), 3),
        "retry_after_failure": round(_bool_rate(prompts, "retry_after_failure"), 3),
        "code_delta_after_prompt": [
            abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0))
            for p in prompts
        ],
        "zero_change_prompt_count": sum(
            1
            for p in prompts
            if abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0)) == 0
        ),
        "response_to_code_correlation": round(
            sum(
                1
                for p in prompts
                if abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0)) > 0
            )
            / max(total_prompts, 1),
            3,
        ),
        "single_focus_prompt_rate": round(
            sum(1 for w in word_counts if 15 <= w <= 180) / max(total_prompts, 1),
            3,
        ),
        "kitchen_sink_prompt_count": sum(1 for w in word_counts if w > 350),
        "error_recovery_score": round(cat_results["approach"]["detailed"]["debugging_score"] * 10, 2),
        "paste_ratio": round(_bool_rate(prompts, "paste_detected"), 3),
        "external_paste_detected": any((p.get("paste_length") or 0) > 400 for p in prompts),
        "solution_dump_detected": any(_is_solution_dump(p.get("message", "")) for p in prompts),
        "injection_attempt": any(
            any(re.search(pat, (p.get("message", "") or "").lower()) for pat in INJECTION_PATTERNS)
            for p in prompts
        ),
        "suspiciously_fast": (total_duration_seconds < 300 and tests_passed > 0),
    }

    # --- Legacy backward-compat weights_used ---
    from .rules import MVP_WEIGHTS

    legacy_weights = {**MVP_WEIGHTS, **(weights or {})}

    return {
        "final_score": final_score,
        "category_scores": category_scores,
        "detailed_scores": detailed_scores,
        "explanations": explanations,
        "per_prompt_scores": per_prompt_scores,
        "component_scores": component_scores,
        "weights_used": legacy_weights,
        "metric_details": metric_details,
        "fraud": fraud,
        "soft_signals": soft_signals,
        "uncapped_final_score": uncapped_final_score,
        "applied_caps": applied_caps,
    }


def generate_heuristic_summary(
    category_scores: Dict[str, Any],
    soft_signals: Dict[str, Any] | None = None,
    fraud_flags: List[str] | None = None,
) -> str:
    """Rule-based recruiter summary grounded in measured scoring signals."""
    scores = category_scores or {}
    fraud_flags = fraud_flags or []
    soft_signals = soft_signals or {}

    task_completion = scores.get("task_completion")
    independence = scores.get("independence")
    prompt_clarity = scores.get("prompt_clarity")
    context_provision = scores.get("context_provision")

    lines: List[str] = []
    if isinstance(task_completion, (int, float)) and isinstance(independence, (int, float)):
        if task_completion >= 8 and independence >= 7:
            lines.append("Strong delivery signal: the candidate completed the task with independent, efficient pacing.")
        elif task_completion < 5:
            lines.append("Delivery risk: task completion is below baseline and likely needs interview follow-up.")
    if isinstance(prompt_clarity, (int, float)) and prompt_clarity >= 7:
        lines.append("Prompts were clear and structured, which usually correlates with faster AI iteration quality.")
    if isinstance(context_provision, (int, float)) and context_provision < 5:
        lines.append("Context sharing was limited; probe debugging context and file/error grounding in the next interview.")

    weak_dimensions = [
        key
        for key, value in scores.items()
        if isinstance(value, (int, float)) and value < 4
    ]
    if weak_dimensions:
        pretty = ", ".join(d.replace("_", " ") for d in weak_dimensions[:2])
        lines.append(f"Significant gap detected in {pretty}; targeted follow-up questions are recommended.")

    prompt_evolution = soft_signals.get("prompt_evolution") if isinstance(soft_signals, dict) else None
    trend = prompt_evolution.get("trend") if isinstance(prompt_evolution, dict) else None
    if trend == "improving":
        lines.append("Prompt quality trended upward during the session, indicating adaptive collaboration behavior.")
    elif trend == "declining":
        lines.append("Prompt quality declined over time, which may indicate pressure-response risk under time constraints.")

    if fraud_flags:
        lines.append(
            "Note: potential integrity flags were detected ("
            + ", ".join(sorted(set(str(flag) for flag in fraud_flags)))
            + "). Human review is recommended."
        )

    if not lines:
        lines.append("Performance is mixed across dimensions; use the weakest categories to drive interview probing.")

    return " ".join(lines[:3]).strip()
