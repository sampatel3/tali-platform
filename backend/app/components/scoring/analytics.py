"""
Heuristic prompt analytics -- non-AI scoring signals.

All functions in this module compute analytics directly from prompt
data and assessment metadata. They do NOT call any external API.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .rules import COPYPASTE_PATTERNS

logger = logging.getLogger(__name__)


def compute_time_to_first_prompt(assessment: Any) -> dict:
    """Seconds from assessment start to first Claude interaction."""
    prompts = assessment.ai_prompts or []
    started_at = assessment.started_at
    if not prompts or not started_at:
        return {"signal": "time_to_first_prompt", "value": None, "flag": None}

    first_ts = prompts[0].get("timestamp")
    if not first_ts:
        return {"signal": "time_to_first_prompt", "value": None, "flag": None}

    try:
        first_dt = datetime.fromisoformat(first_ts)
        if first_dt.tzinfo is None:
            first_dt = first_dt.replace(tzinfo=timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        seconds = int((first_dt - started_at).total_seconds())
        flag = "rushed" if seconds < 30 else ("deliberate" if seconds > 300 else None)
        return {"signal": "time_to_first_prompt", "value": seconds, "flag": flag}
    except Exception as e:
        logger.warning("Failed to compute time_to_first_prompt: %s", e)
        return {"signal": "time_to_first_prompt", "value": None, "flag": None}


def compute_prompt_speed(prompts: list) -> dict:
    """Average time between consecutive prompts in milliseconds."""
    if len(prompts) < 2:
        return {"signal": "prompt_speed", "value": None, "avg_ms": None, "flag": None}

    gaps = []
    for i in range(1, len(prompts)):
        ms = prompts[i].get("time_since_last_prompt_ms")
        if ms is not None:
            gaps.append(ms)

    if not gaps:
        # Fall back to computing from timestamps
        for i in range(1, len(prompts)):
            try:
                t1 = datetime.fromisoformat(prompts[i - 1].get("timestamp", ""))
                t2 = datetime.fromisoformat(prompts[i].get("timestamp", ""))
                gaps.append(int((t2 - t1).total_seconds() * 1000))
            except Exception:
                continue

    if not gaps:
        return {"signal": "prompt_speed", "value": None, "avg_ms": None, "flag": None}

    avg_ms = int(sum(gaps) / len(gaps))
    flag = "rapid_fire" if avg_ms < 15000 else None  # Less than 15 seconds average
    return {"signal": "prompt_speed", "value": len(gaps), "avg_ms": avg_ms, "flag": flag}


def compute_prompt_frequency(prompts: list, assessment_duration_seconds: int | None) -> dict:
    """Total count and prompts-per-10-minute-window breakdown."""
    total = len(prompts)
    windows = []

    if assessment_duration_seconds and assessment_duration_seconds > 0:
        num_windows = max(1, assessment_duration_seconds // 600 + 1)
        window_counts = [0] * num_windows

        started = None
        if prompts and prompts[0].get("timestamp"):
            try:
                started = datetime.fromisoformat(prompts[0].get("timestamp"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except Exception:
                started = None

        for p in prompts:
            ts = p.get("timestamp")
            if not ts or not started:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                elapsed = (dt - started).total_seconds()
                window_idx = min(int(elapsed // 600), num_windows - 1)
                window_counts[window_idx] += 1
            except Exception:
                continue

        windows = [{"window": i, "count": c} for i, c in enumerate(window_counts)]

    return {
        "signal": "prompt_frequency",
        "total": total,
        "windows": windows,
        "flag": "excessive" if total > 30 else None,
    }


def compute_prompt_length_stats(prompts: list) -> dict:
    """Average, min, max word count with flags for outliers."""
    if not prompts:
        return {
            "signal": "prompt_length_stats",
            "avg_words": 0, "min_words": 0, "max_words": 0,
            "very_short_count": 0, "very_long_count": 0,
            "flag": None,
        }

    word_counts = [p.get("word_count", len(p.get("message", "").split())) for p in prompts]
    avg_words = int(sum(word_counts) / len(word_counts)) if word_counts else 0
    min_words = min(word_counts) if word_counts else 0
    max_words = max(word_counts) if word_counts else 0
    very_short = sum(1 for w in word_counts if w < 10)
    very_long = sum(1 for w in word_counts if w > 500)

    flag = None
    if very_short > len(prompts) * 0.5:
        flag = "mostly_very_short"
    elif very_long > len(prompts) * 0.3:
        flag = "many_very_long"

    return {
        "signal": "prompt_length_stats",
        "avg_words": avg_words,
        "min_words": min_words,
        "max_words": max_words,
        "very_short_count": very_short,
        "very_long_count": very_long,
        "flag": flag,
    }


def detect_copy_paste(prompts: list) -> dict:
    """Detect potential copy-paste from external sources."""
    flags = []
    paste_count = sum(1 for p in prompts if p.get("paste_detected"))

    for i, p in enumerate(prompts):
        message = p.get("message", "")
        # Check for known patterns
        for pattern in COPYPASTE_PATTERNS:
            if re.search(pattern, message):
                flags.append({
                    "prompt_index": i,
                    "type": "pattern_match",
                    "pattern": pattern[:50],
                    "confidence": 0.6,
                })
                break

        # Check for suspiciously long prompts that were pasted
        if p.get("paste_detected") and len(message) > 500:
            flags.append({
                "prompt_index": i,
                "type": "large_paste",
                "char_count": len(message),
                "confidence": 0.7,
            })

        # Check for prompts that contain complete code solutions
        code_lines = [line for line in message.split("\n") if line.strip() and not line.strip().startswith("#")]
        if len(code_lines) > 20:
            flags.append({
                "prompt_index": i,
                "type": "possible_solution_dump",
                "code_lines": len(code_lines),
                "confidence": 0.5,
            })

    return {
        "signal": "copy_paste_detection",
        "paste_event_count": paste_count,
        "flags": flags,
        "flag": "suspicious" if len(flags) > 2 else None,
    }


def compute_code_delta(prompts: list) -> dict:
    """Diff size between code_before and code_after per prompt."""
    deltas = []
    total_change = 0

    for i, p in enumerate(prompts):
        before = p.get("code_before", "") or ""
        after = p.get("code_after", "") or ""
        if not before and not after:
            deltas.append({"index": i, "delta_chars": 0, "changed": False})
            continue
        delta = abs(len(after) - len(before))
        changed = before != after
        deltas.append({"index": i, "delta_chars": delta, "changed": changed})
        total_change += delta

    prompts_with_change = sum(1 for d in deltas if d["changed"])
    utilization_rate = prompts_with_change / len(prompts) if prompts else 0

    return {
        "signal": "code_delta",
        "deltas": deltas,
        "total_change_chars": total_change,
        "prompts_with_code_change": prompts_with_change,
        "utilization_rate": round(utilization_rate, 2),
        "flag": "low_utilization" if len(prompts) > 3 and utilization_rate < 0.3 else None,
    }


def compute_self_correction_rate(prompts: list) -> dict:
    """How often candidate modifies code rather than using AI response verbatim."""
    if len(prompts) < 2:
        return {"signal": "self_correction_rate", "rate": None, "flag": None}

    modifications = 0
    comparable = 0

    for i in range(1, len(prompts)):
        before = prompts[i].get("code_before", "") or ""
        prev_after = prompts[i - 1].get("code_after", "") or ""
        if not before or not prev_after:
            continue
        comparable += 1
        # If code_before of this prompt differs from code_after of previous,
        # candidate made independent edits between prompts
        if before != prev_after:
            modifications += 1

    rate = modifications / comparable if comparable > 0 else None
    return {
        "signal": "self_correction_rate",
        "rate": round(rate, 2) if rate is not None else None,
        "modifications": modifications,
        "comparable_pairs": comparable,
        "flag": None,
    }


def compute_token_efficiency(prompts: list, tests_passed: int | None, tests_total: int | None) -> dict:
    """Total tokens consumed vs. problems solved."""
    total_tokens = sum(p.get("tokens_used", 0) or (p.get("input_tokens", 0) + p.get("output_tokens", 0)) for p in prompts)
    solve_rate = (tests_passed / tests_total) if tests_total and tests_total > 0 else 0

    tokens_per_solve = total_tokens / max(tests_passed or 0, 1) if tests_passed else None
    efficiency = None
    if total_tokens > 0 and solve_rate > 0:
        # Higher is better: solve_rate per 1000 tokens
        efficiency = round(solve_rate / (total_tokens / 1000), 3)

    return {
        "signal": "token_efficiency",
        "total_tokens": total_tokens,
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "solve_rate": round(solve_rate, 2),
        "tokens_per_solve": tokens_per_solve,
        "efficiency_score": efficiency,
        "flag": "token_heavy" if total_tokens > 50000 else None,
    }


def compute_browser_focus_ratio(prompts: list, assessment_duration_seconds: int | None) -> dict:
    """Percentage of assessment time browser was in focus."""
    if not prompts:
        return {"signal": "browser_focus_ratio", "ratio": None, "flag": None}

    focused_count = sum(1 for p in prompts if p.get("browser_focused", True))
    total = len(prompts)
    ratio = focused_count / total if total > 0 else 1.0

    flag = None
    if ratio < 0.8:
        flag = "low_focus"
    if ratio < 0.5:
        flag = "very_low_focus"

    return {
        "signal": "browser_focus_ratio",
        "ratio": round(ratio, 2),
        "focused_prompts": focused_count,
        "total_prompts": total,
        "flag": flag,
    }


def compute_tab_switch_count(assessment: Any) -> dict:
    """Total tab switches recorded during assessment."""
    count = getattr(assessment, "tab_switch_count", 0) or 0
    flag = None
    if count > 10:
        flag = "excessive_switching"
    elif count > 5:
        flag = "frequent_switching"

    return {
        "signal": "tab_switch_count",
        "count": count,
        "flag": flag,
    }


def compute_all_heuristics(assessment: Any, prompts: list | None = None) -> dict:
    """Compute all heuristic signals and return a combined dict."""
    if prompts is None:
        prompts = assessment.ai_prompts or []

    # Compute assessment duration
    duration_seconds = None
    if assessment.started_at and assessment.completed_at:
        started = assessment.started_at
        completed = assessment.completed_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        duration_seconds = int((completed - started).total_seconds())

    results = {
        "time_to_first_prompt": compute_time_to_first_prompt(assessment),
        "prompt_speed": compute_prompt_speed(prompts),
        "prompt_frequency": compute_prompt_frequency(prompts, duration_seconds),
        "prompt_length_stats": compute_prompt_length_stats(prompts),
        "copy_paste_detection": detect_copy_paste(prompts),
        "code_delta": compute_code_delta(prompts),
        "self_correction_rate": compute_self_correction_rate(prompts),
        "token_efficiency": compute_token_efficiency(
            prompts,
            getattr(assessment, "tests_passed", None),
            getattr(assessment, "tests_total", None),
        ),
        "browser_focus_ratio": compute_browser_focus_ratio(prompts, duration_seconds),
        "tab_switch_count": compute_tab_switch_count(assessment),
    }

    # Collect all flags
    all_flags = [r.get("flag") for r in results.values() if r.get("flag")]

    results["_summary"] = {
        "total_signals": len(results) - 1,  # Exclude _summary
        "flags": all_flags,
        "has_warnings": len(all_flags) > 0,
    }

    return results
