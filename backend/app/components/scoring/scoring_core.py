"""MVP Scoring Engine — 30+ metrics across 8 categories (all heuristic).

GUARDRAIL: No ML, no HuggingFace. The only Claude call is CV-job fit
matching (separate service). Everything here is regex + math.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .rules import (
    VAGUE_PATTERNS,
    INJECTION_PATTERNS,
)

# ---------------------------------------------------------------------------
# Category weights (must sum to 1.0 when CV match is included)
# ---------------------------------------------------------------------------
CATEGORY_WEIGHTS = {
    "task_completion": 0.20,
    "prompt_clarity": 0.15,
    "context_provision": 0.15,
    "independence": 0.20,
    "utilization": 0.10,
    "communication": 0.10,
    "approach": 0.05,
    "cv_match": 0.05,
}

# ---------------------------------------------------------------------------
# Communication patterns
# ---------------------------------------------------------------------------
UNPROFESSIONAL_PATTERNS = [
    r"\b(wtf|omg|lol|lmao|bruh)\b",
    r"!!!+",
    r"\?\?\?+",
    r"^(ugh|argh|damn|shit|fuck)",
]
SEVERE_UNPROFESSIONAL_PATTERNS = [
    (r"\bfuck(?:\s+off|\s+you|ing|ed|er)?\b", "explicit_profanity"),
    (r"\bstfu\b", "hostile_directive"),
    (r"\b(shithead|dumbass|idiot|moron)\b", "insult"),
    (r"\b(kill yourself|kys)\b", "abusive_threat"),
]
SEVERE_COMMUNICATION_SCORE_CAP = 2.0
SEVERE_LANGUAGE_FINAL_SCORE_CAP = 35.0
FILLER_WORDS = ["um", "uh", "like", "basically", "actually", "just", "really", "very"]

DEBUGGING_PATTERNS = [
    r"(print|log|console\.log|debug)",
    r"(error|exception|traceback|stack)",
    r"(step by step|one at a time|isolat)",
    r"(hypothesis|theory|suspect|might be)",
]
DESIGN_PATTERNS = [
    r"(architect|structure|design|pattern)",
    r"(tradeoff|trade-off|pros and cons|alternative)",
    r"(scalab|maintain|extend|modular)",
    r"(edge case|corner case|what if)",
    r"(performance|efficiency|complexity)",
]

ATTEMPT_PATTERNS = [
    r"I tried",
    r"I've tried",
    r"I attempted",
    r"expected .+ but got",
    r"it should .+ but instead",
    r"already .+ without success",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _clamp10(v: float) -> float:
    return max(0.0, min(10.0, round(float(v), 1)))


def _count_words(text: str) -> int:
    return len((text or "").split())


def _bool_rate(items: List[Dict[str, Any]], key: str) -> float:
    if not items:
        return 0.0
    return sum(1 for i in items if i.get(key)) / len(items)


def _question_presence_rate(items: List[Dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return sum(1 for i in items if (i.get("question_count") or 0) > 0 or "?" in (i.get("message", "") or "")) / len(items)


def _is_solution_dump(prompt: str) -> bool:
    text = prompt or ""
    return _count_words(text) > 500 and (text.count("def ") + text.count("function ")) > 3


def _pattern_rate(prompts: List[Dict], patterns: list) -> float:
    """Fraction of prompts that match at least one pattern."""
    if not prompts:
        return 0.0
    count = 0
    for p in prompts:
        msg = (p.get("message", "") or "").lower()
        if any(re.search(pat, msg, re.IGNORECASE) for pat in patterns):
            count += 1
    return count / len(prompts)


def _is_vague_prompt(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(re.search(pat, text, re.IGNORECASE) for pat in VAGUE_PATTERNS)


def _prompt_has_context(prompt: Dict[str, Any]) -> bool:
    return bool(
        prompt.get("code_snippet_included")
        or prompt.get("error_message_included")
        or prompt.get("line_number_referenced")
        or prompt.get("file_reference")
    )


def _extract_prompt_metadata(prompt_text: str) -> Dict[str, Any]:
    """Infer prompt metadata when upstream capture fields are missing."""
    text = str(prompt_text or "")
    lowered = text.lower()
    has_code_fence = "```" in text
    has_indented_code = bool(re.search(r"(?m)^(?: {4}|\t)\S", text))
    code_snippet_included = has_code_fence or has_indented_code

    error_message_included = bool(
        re.search(
            r"(?i)(traceback|error:|exception|failed|assert|stack trace|syntaxerror|typeerror|valueerror)",
            text,
        )
    )
    line_number_referenced = bool(
        re.search(r"(?i)\bline\s+\d+\b|:\d+(?::\d+)?\b", text)
    )
    file_reference = bool(
        re.search(
            r"(?i)\b(?:src/|app/|tests?/|backend/|frontend/|[\w\-.]+\.(?:py|js|jsx|ts|tsx|json|yml|yaml|md))\b",
            text,
        )
    )
    references_previous = any(re.search(pat, text, re.IGNORECASE) for pat in ATTEMPT_PATTERNS)
    retry_after_failure = bool(
        re.search(
            r"(?i)\b(retry|tried again|try again|another attempt|failed previously|after it failed)\b",
            lowered,
        )
    )

    return {
        "word_count": _count_words(text),
        "question_count": text.count("?"),
        "code_snippet_included": code_snippet_included,
        "error_message_included": error_message_included,
        "line_number_referenced": line_number_referenced,
        "file_reference": file_reference,
        "references_previous": references_previous,
        "retry_after_failure": retry_after_failure,
    }


def _score_reasoning_depth(prompt: str) -> float:
    """Score debugging/design reasoning depth on a 0-3 scale."""
    text = str(prompt or "")
    lowered = text.lower()
    has_signal = any(re.search(pat, lowered, re.IGNORECASE) for pat in DEBUGGING_PATTERNS + DESIGN_PATTERNS)
    if not has_signal:
        return 0.0

    has_partial_specificity = bool(
        re.search(
            r"(?i)\b(function|module|class|endpoint|api|query|database|cache|service|"
            r"tradeoff|trade-off|edge case|vs|versus|null reference|timeout|variable|state)\b",
            text,
        )
    )
    has_concrete_grounding = bool(
        re.search(
            r"(?i)\b(line\s+\d+|file\s+[\w\-.]+|src/|tests?/|[\w\-.]+\.(?:py|js|ts|tsx|jsx|json|yml|yaml))\b|:\d+(?::\d+)?\b",
            text,
        )
    )
    has_hypothesis = bool(
        re.search(r"(?i)\b(hypothesis|i think|i suspect|likely|probably|might be|root cause)\b", text)
    )
    has_test_plan = bool(
        re.search(
            r"(?i)\b(let me|i will)\s+(check|test|verify|inspect|reproduce|confirm)\b|"
            r"\b(add|insert|use)\s+(logging|logs|print|instrumentation)\b",
            text,
        )
    )

    if has_hypothesis and has_test_plan:
        return 3.0
    if has_concrete_grounding:
        return 2.0
    if has_hypothesis or has_partial_specificity:
        return 1.0
    return 0.0


def _score_prompt_evolution(prompts: list) -> Dict[str, Any]:
    """Track whether prompt quality improves over the session."""
    if not prompts:
        return {
            "early_specificity": 0.0,
            "mid_specificity": 0.0,
            "late_specificity": 0.0,
            "early_context": 0.0,
            "mid_context": 0.0,
            "late_context": 0.0,
            "early_word_count": 0.0,
            "mid_word_count": 0.0,
            "late_word_count": 0.0,
            "trend": "stable",
        }

    total = len(prompts)
    first_cut = max(1, total // 3)
    second_cut = max(first_cut + 1, (2 * total) // 3) if total > 1 else first_cut
    early = prompts[:first_cut]
    mid = prompts[first_cut:second_cut] or prompts[first_cut:first_cut + 1]
    late = prompts[second_cut:] or prompts[-1:]

    def _segment_rates(segment: list[Dict[str, Any]]) -> tuple[float, float, float]:
        if not segment:
            return (0.0, 0.0, 0.0)
        specificity = sum(1 for prompt in segment if not _is_vague_prompt(prompt.get("message", ""))) / len(segment)
        context = sum(1 for prompt in segment if _prompt_has_context(prompt)) / len(segment)
        avg_words = sum(_count_words(prompt.get("message", "")) for prompt in segment) / len(segment)
        return (specificity, context, avg_words)

    early_spec, early_ctx, early_words = _segment_rates(early)
    mid_spec, mid_ctx, mid_words = _segment_rates(mid)
    late_spec, late_ctx, late_words = _segment_rates(late)

    spec_delta = late_spec - early_spec
    context_delta = late_ctx - early_ctx
    if spec_delta <= -0.15 or context_delta <= -0.15:
        trend = "declining"
    elif spec_delta >= 0.15 or context_delta >= 0.20:
        trend = "improving"
    else:
        trend = "stable"

    return {
        "early_specificity": round(early_spec, 3),
        "mid_specificity": round(mid_spec, 3),
        "late_specificity": round(late_spec, 3),
        "early_context": round(early_ctx, 3),
        "mid_context": round(mid_ctx, 3),
        "late_context": round(late_ctx, 3),
        "early_word_count": round(early_words, 1),
        "mid_word_count": round(mid_words, 1),
        "late_word_count": round(late_words, 1),
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# CATEGORY 1: Task Completion (3 metrics)
# ---------------------------------------------------------------------------

def _score_task_completion(
    tests_passed: int,
    tests_total: int,
    duration_minutes: float,
    time_limit_minutes: int,
) -> Dict[str, Any]:
    """Tests pass rate, time compliance, and time efficiency."""
    # tests_passed_ratio (0-10)
    ratio = (tests_passed / tests_total) if tests_total > 0 else 0.0
    tests_passed_score = _clamp10(ratio * 10.0)

    # time_compliance — did they finish within the limit? (0-10)
    if time_limit_minutes > 0 and duration_minutes > 0:
        if duration_minutes <= time_limit_minutes:
            time_compliance = 10.0
        elif duration_minutes <= time_limit_minutes * 1.25:
            time_compliance = 7.0
        elif duration_minutes <= time_limit_minutes * 1.5:
            time_compliance = 4.0
        else:
            time_compliance = 1.0
    else:
        time_compliance = 5.0
    time_compliance = _clamp10(time_compliance)

    # time_efficiency — how much of the limit did they use? (0-10)
    if time_limit_minutes > 0 and duration_minutes > 0:
        usage = duration_minutes / time_limit_minutes
        if usage <= 0.5:
            time_eff = 9.0
        elif usage <= 0.8:
            time_eff = 8.0
        elif usage <= 1.0:
            time_eff = 7.0
        elif usage <= 1.25:
            time_eff = 4.0
        else:
            time_eff = 2.0
    else:
        time_eff = 5.0
    time_efficiency = _clamp10(time_eff)

    # Category score = average of the 3
    cat_score = _clamp10((tests_passed_score + time_compliance + time_efficiency) / 3.0)

    return {
        "score": cat_score,
        "detailed": {
            "tests_passed_ratio": tests_passed_score,
            "time_compliance": time_compliance,
            "time_efficiency": time_efficiency,
        },
        "explanations": {
            "tests_passed_ratio": f"Passed {tests_passed}/{tests_total} tests ({ratio*100:.0f}%).",
            "time_compliance": f"Completed in {duration_minutes:.0f}m of {time_limit_minutes}m limit.",
            "time_efficiency": f"Used {(duration_minutes/time_limit_minutes*100):.0f}% of allowed time." if time_limit_minutes > 0 else "No time limit set.",
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 2: Prompt Clarity (4 metrics)
# ---------------------------------------------------------------------------

def _score_prompt_clarity(prompts: list) -> Dict[str, Any]:
    word_counts = [_count_words(p.get("message", "")) for p in prompts]
    total = len(prompts) or 1

    # prompt_length_quality — sweet spot 20-150 words
    sweet = sum(1 for w in word_counts if 20 <= w <= 150)
    too_short = sum(1 for w in word_counts if w < 10)
    prompt_length_quality = _clamp10((sweet / total) * 9.0 - (too_short / total) * 3.0 + 1.0)

    # question_clarity — % containing questions
    q_rate = _question_presence_rate(prompts)
    question_clarity = _clamp10(q_rate * 10.0)

    # prompt_specificity — non-vague prompts
    vague_count = sum(
        1 for p in prompts
        if _is_vague_prompt(p.get("message", ""))
    )
    spec_rate = (total - vague_count) / total
    prompt_specificity = _clamp10(spec_rate * 10.0)

    # vagueness_score — inverse of vague patterns (high = good)
    vagueness_score = _clamp10((1.0 - vague_count / total) * 10.0)

    cat_score = _clamp10((prompt_length_quality + question_clarity + prompt_specificity + vagueness_score) / 4.0)

    return {
        "score": cat_score,
        "detailed": {
            "prompt_length_quality": prompt_length_quality,
            "question_clarity": question_clarity,
            "prompt_specificity": prompt_specificity,
            "vagueness_score": vagueness_score,
        },
        "explanations": {
            "prompt_length_quality": f"{sweet}/{total} prompts were in the ideal 20-150 word range.",
            "question_clarity": f"{q_rate*100:.0f}% of prompts contained clear questions.",
            "prompt_specificity": f"{total - vague_count}/{total} prompts were specific and targeted.",
            "vagueness_score": f"{vague_count} vague prompt(s) detected." if vague_count else "No vague prompts detected.",
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 3: Context Provision (4 metrics)
# ---------------------------------------------------------------------------

def _score_context_provision(prompts: list) -> Dict[str, Any]:
    total = len(prompts) or 1

    code_rate = _bool_rate(prompts, "code_snippet_included")
    code_context = _clamp10(code_rate * 10.0)

    error_rate = _bool_rate(prompts, "error_message_included")
    error_context = _clamp10(error_rate * 10.0)

    line_rate = _bool_rate(prompts, "line_number_referenced")
    file_rate = _bool_rate(prompts, "file_reference")
    reference_score = _clamp10((line_rate + file_rate) * 5.0)

    # Prior attempt mention
    attempt_rate = _pattern_rate(prompts, ATTEMPT_PATTERNS)
    attempt_score = _clamp10(attempt_rate * 10.0)

    cat_score = _clamp10((code_context + error_context + reference_score + attempt_score) / 4.0)

    return {
        "score": cat_score,
        "detailed": {
            "code_context_rate": code_context,
            "error_context_rate": error_context,
            "reference_rate": reference_score,
            "attempt_mention_rate": attempt_score,
        },
        "explanations": {
            "code_context_rate": f"{code_rate*100:.0f}% of prompts included code snippets.",
            "error_context_rate": f"{error_rate*100:.0f}% of prompts included error messages.",
            "reference_rate": f"Referenced specific lines/files in {(line_rate+file_rate)*50:.0f}% of prompts.",
            "attempt_mention_rate": f"Mentioned prior attempts in {attempt_rate*100:.0f}% of prompts.",
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 4: Independence & Efficiency (5 metrics)
# ---------------------------------------------------------------------------

def _score_independence(
    prompts: list,
    tests_passed: int,
    total_tokens: int,
    min_reading_time_seconds: int | None = None,
) -> Dict[str, Any]:
    total = len(prompts) or 1

    # first_prompt_delay (task-aware thresholds; defaults tuned for complex repos)
    first_sec = ((prompts[0].get("time_since_assessment_start_ms") or 0) / 1000.0) if prompts else 0
    top_threshold = max(60, int(min_reading_time_seconds or 300))
    strong_threshold = max(60, int(round(top_threshold * 0.6)))
    if strong_threshold >= top_threshold:
        strong_threshold = max(60, top_threshold - 30)

    if first_sec >= top_threshold:
        delay_score = 10.0
    elif first_sec >= strong_threshold:
        delay_score = 8.0
    elif first_sec >= 60:
        delay_score = 5.0
    elif first_sec >= 30:
        delay_score = 3.0
    else:
        delay_score = 1.0
    first_prompt_delay = _clamp10(delay_score)

    # prompt_spacing — avg gap between prompts (>60s good)
    gaps = [(p.get("time_since_last_prompt_ms") or 0) / 1000.0 for p in prompts[1:] if p.get("time_since_last_prompt_ms")]
    avg_gap = (sum(gaps) / len(gaps)) if gaps else 0
    if avg_gap >= 120:
        spacing = 9.0
    elif avg_gap >= 60:
        spacing = 7.0
    elif avg_gap >= 30:
        spacing = 5.0
    else:
        spacing = 2.0
    prompt_spacing = _clamp10(spacing)

    # prompt_efficiency — fewer prompts per test = better
    ppt = total / max(tests_passed, 1) if tests_passed > 0 else float(total)
    if ppt <= 1.5:
        pe = 10.0
    elif ppt <= 3.0:
        pe = 7.0
    elif ppt <= 5.0:
        pe = 5.0
    else:
        pe = 2.0
    prompt_efficiency = _clamp10(pe)

    # token_efficiency — tokens per test
    tpt = total_tokens / max(tests_passed, 1) if tests_passed > 0 else float(total_tokens)
    if tpt <= 500:
        te = 10.0
    elif tpt <= 1000:
        te = 8.0
    elif tpt <= 2000:
        te = 6.0
    elif tpt <= 5000:
        te = 4.0
    else:
        te = 2.0
    token_efficiency = _clamp10(te)

    # pre_prompt_effort — candidate changes code before prompting
    code_changes = sum(1 for p in prompts if (p.get("code_before", "") or "") != (p.get("code_after", "") or ""))
    effort_rate = code_changes / total
    pre_prompt_effort = _clamp10(effort_rate * 10.0)

    cat_score = _clamp10(
        (first_prompt_delay + prompt_spacing + prompt_efficiency + token_efficiency + pre_prompt_effort) / 5.0
    )

    return {
        "score": cat_score,
        "detailed": {
            "first_prompt_delay": first_prompt_delay,
            "prompt_spacing": prompt_spacing,
            "prompt_efficiency": prompt_efficiency,
            "token_efficiency": token_efficiency,
            "pre_prompt_effort": pre_prompt_effort,
        },
        "explanations": {
            "first_prompt_delay": (
                f"Candidate waited {first_sec:.0f}s before first prompt "
                f"(task baseline: {top_threshold}s for top score)."
            ) + (" Good self-reliance." if first_sec >= strong_threshold else " Prompted quickly."),
            "prompt_spacing": f"Average {avg_gap:.0f}s between prompts." + (" Thoughtful pacing." if avg_gap >= 60 else " Rapid prompting."),
            "prompt_efficiency": f"{ppt:.1f} prompts per test passed." + (" Efficient." if ppt <= 2 else ""),
            "token_efficiency": f"{tpt:.0f} tokens per test passed.",
            "pre_prompt_effort": f"Changed code before {effort_rate*100:.0f}% of prompts.",
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 5: Response Utilization (3 metrics)
# ---------------------------------------------------------------------------

def _score_utilization(prompts: list) -> Dict[str, Any]:
    total = len(prompts) or 1

    # post_prompt_changes — did code change after prompts?
    deltas = [
        abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0))
        for p in prompts
    ]
    change_rate = sum(1 for d in deltas if d > 0) / total
    post_prompt_changes = _clamp10(change_rate * 10.0)

    # wasted_prompts — prompts with zero code change (inverse scoring)
    zero_change = sum(1 for d in deltas if d == 0)
    wasted = _clamp10((1.0 - zero_change / total) * 10.0)

    # iteration_quality — builds on previous
    builds = _bool_rate(prompts, "references_previous")
    retry = _bool_rate(prompts, "retry_after_failure")
    iteration_quality = _clamp10((builds * 5.0) + (retry * 5.0))

    cat_score = _clamp10((post_prompt_changes + wasted + iteration_quality) / 3.0)

    return {
        "score": cat_score,
        "detailed": {
            "post_prompt_changes": post_prompt_changes,
            "wasted_prompts": wasted,
            "iteration_quality": iteration_quality,
        },
        "explanations": {
            "post_prompt_changes": f"Applied AI suggestions in {change_rate*100:.0f}% of prompts.",
            "wasted_prompts": f"{zero_change} prompt(s) with no resulting code changes." if zero_change else "Every prompt led to code changes.",
            "iteration_quality": f"Builds on previous responses: {builds*100:.0f}%. Retries after failure: {retry*100:.0f}%.",
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 6: Communication Quality (3 metrics)
# ---------------------------------------------------------------------------

def _score_communication(prompts: list) -> Dict[str, Any]:
    all_messages = [(p.get("message", "") or "") for p in prompts]
    total = len(prompts) or 1

    # grammar_score — check for lowercase 'i', random caps, double spaces
    grammar_issues = 0
    grammar_detail = []
    for msg in all_messages:
        # Standalone lowercase 'i'
        if re.search(r"(?<!\w)i(?!\w)(?!')", msg):
            grammar_issues += 1
            grammar_detail.append("lowercase_i")
        # Double spaces
        if "  " in msg:
            grammar_issues += 1
            grammar_detail.append("double_space")
        # No punctuation at end of a multi-word message
        words = msg.strip().split()
        if len(words) > 5 and not msg.strip()[-1] in ".!?:":
            grammar_issues += 1
            grammar_detail.append("no_end_punctuation")
    issue_rate = grammar_issues / max(total * 3, 1)  # Max 3 checks per prompt
    grammar_score = _clamp10(10.0 - issue_rate * 15.0)

    # readability_score — sentence length sweet spot 10-20 words
    sentence_lens = []
    for msg in all_messages:
        sentences = re.split(r"[.!?]+", msg)
        for s in sentences:
            wc = len(s.split())
            if wc > 2:
                sentence_lens.append(wc)
    if sentence_lens:
        avg_sent = sum(sentence_lens) / len(sentence_lens)
        # 10-20 is ideal
        if 10 <= avg_sent <= 20:
            read = 9.0
        elif 7 <= avg_sent <= 25:
            read = 7.0
        elif 5 <= avg_sent <= 35:
            read = 5.0
        else:
            read = 3.0
    else:
        read = 5.0
    readability_score = _clamp10(read)

    # professional_tone — check for unprofessional patterns and filler words
    unprofessional = 0
    filler_count = 0
    severe_unprofessional = 0
    severe_terms: list[str] = []
    for msg in all_messages:
        lower = msg.lower()
        for pat in UNPROFESSIONAL_PATTERNS:
            if re.search(pat, lower):
                unprofessional += 1
                break
        for pat, label in SEVERE_UNPROFESSIONAL_PATTERNS:
            if re.search(pat, lower):
                severe_unprofessional += 1
                severe_terms.append(label)
                break
        for fw in FILLER_WORDS:
            filler_count += len(re.findall(r"\b" + fw + r"\b", lower))

    filler_rate = filler_count / max(sum(_count_words(m) for m in all_messages), 1)
    tone_score = _clamp10(10.0 - unprofessional * 2.0 - filler_rate * 20.0)
    if severe_unprofessional > 0:
        tone_score = min(tone_score, 1.0)

    cat_score = _clamp10((grammar_score + readability_score + tone_score) / 3.0)
    if severe_unprofessional > 0:
        cat_score = min(cat_score, SEVERE_COMMUNICATION_SCORE_CAP)

    grammar_issue_summary = f"{grammar_issues} issue(s) detected" if grammar_issues else "Clean writing"
    if grammar_detail:
        unique_issues = list(set(grammar_detail))[:3]
        grammar_issue_summary += f" ({', '.join(unique_issues)})."
    else:
        grammar_issue_summary += "."

    return {
        "score": cat_score,
        "detailed": {
            "grammar_score": grammar_score,
            "readability_score": readability_score,
            "tone_score": tone_score,
        },
        "explanations": {
            "grammar_score": grammar_issue_summary,
            "readability_score": f"Average sentence length: {sum(sentence_lens)/len(sentence_lens):.0f} words." if sentence_lens else "Not enough sentences to evaluate.",
            "tone_score": (
                f"{'No' if unprofessional == 0 else str(unprofessional)} unprofessional pattern(s); "
                f"{severe_unprofessional} severe abuse event(s); filler word rate: {filler_rate*100:.1f}%."
            ),
        },
        "flags": {
            "severe_unprofessional_language": severe_unprofessional > 0,
            "severe_unprofessional_count": severe_unprofessional,
            "severe_terms": sorted(set(severe_terms)),
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 7: Debugging & Design (2 metrics)
# ---------------------------------------------------------------------------

def _score_approach(prompts: list) -> Dict[str, Any]:
    debug_rate = _pattern_rate(prompts, DEBUGGING_PATTERNS)
    design_rate = _pattern_rate(prompts, DESIGN_PATTERNS)

    debug_depth_values = []
    design_depth_values = []
    for prompt in prompts:
        msg = prompt.get("message", "") or ""
        depth = _score_reasoning_depth(msg)
        if any(re.search(pat, msg, re.IGNORECASE) for pat in DEBUGGING_PATTERNS):
            debug_depth_values.append(depth)
        if any(re.search(pat, msg, re.IGNORECASE) for pat in DESIGN_PATTERNS):
            design_depth_values.append(depth)

    debug_depth = (sum(debug_depth_values) / len(debug_depth_values)) if debug_depth_values else 0.0
    design_depth = (sum(design_depth_values) / len(design_depth_values)) if design_depth_values else 0.0

    # Presence matters, but reasoning depth contributes most of the score.
    debugging_score = _clamp10((debug_rate * 4.0) + ((debug_depth / 3.0) * 6.0))
    design_score = _clamp10((design_rate * 4.0) + ((design_depth / 3.0) * 6.0))

    cat_score = _clamp10((debugging_score + design_score) / 2.0)

    return {
        "score": cat_score,
        "detailed": {
            "debugging_score": debugging_score,
            "design_score": design_score,
        },
        "explanations": {
            "debugging_score": (
                f"{debug_rate*100:.0f}% of prompts showed debugging strategy; "
                f"reasoning depth {debug_depth:.1f}/3."
            ),
            "design_score": (
                f"{design_rate*100:.0f}% of prompts referenced design/architecture; "
                f"reasoning depth {design_depth:.1f}/3."
            ),
        },
    }


# ---------------------------------------------------------------------------
# CATEGORY 8: CV-Job Match (3 metrics) — populated externally by fit_matching_service
# ---------------------------------------------------------------------------

def _score_cv_match(cv_match: Dict[str, Any] | None) -> Dict[str, Any]:
    """Receive the pre-computed match from fit_matching_service."""
    if not cv_match or cv_match.get("cv_job_match_score") is None:
        return {
            "score": None,
            "detailed": {
                "cv_job_match_score": None,
                "skills_match": None,
                "experience_relevance": None,
            },
            "explanations": {
                "cv_job_match_score": "No CV or job spec available — fit scoring skipped.",
                "skills_match": "Not available.",
                "experience_relevance": "Not available.",
            },
        }

    overall = _clamp10(cv_match.get("cv_job_match_score") or 0)
    skills = _clamp10(cv_match.get("skills_match") or 0)
    experience = _clamp10(cv_match.get("experience_relevance") or 0)

    details = cv_match.get("match_details", {})

    return {
        "score": overall,
        "detailed": {
            "cv_job_match_score": overall,
            "skills_match": skills,
            "experience_relevance": experience,
        },
        "explanations": {
            "cv_job_match_score": details.get("summary", f"Overall fit: {overall}/10."),
            "skills_match": f"Matching: {', '.join(details.get('matching_skills', [])[:5])}. Missing: {', '.join(details.get('missing_skills', [])[:5])}." if details.get("matching_skills") else f"Skills match: {skills}/10.",
            "experience_relevance": f"Experience highlights: {'; '.join(details.get('experience_highlights', [])[:3])}." if details.get("experience_highlights") else f"Experience relevance: {experience}/10.",
        },
    }


# ---------------------------------------------------------------------------
# Per-prompt scoring
# ---------------------------------------------------------------------------

def _compute_per_prompt_scores(prompts: list) -> List[Dict[str, Any]]:
    per_prompt = []
    for p in prompts:
        msg = p.get("message", "") or ""
        wc = _count_words(msg)
        has_question = (p.get("question_count") or msg.count("?")) > 0
        is_sweet_spot = 20 <= wc <= 150
        is_vague = _is_vague_prompt(msg)
        has_context = _prompt_has_context(p)
        has_code_delta = abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0)) > 0

        pp_clarity = _clamp(
            (7.0 if is_sweet_spot else max(0, 7.0 - abs(wc - 80) / 15.0))
            + (2.0 if has_question else 0.0)
            + (1.0 if not is_vague else -2.0),
            0.0, 10.0,
        )
        pp_specificity = _clamp(
            (4.0 if has_context else 1.0)
            + (3.0 if p.get("code_snippet_included") else 0.0)
            + (2.0 if p.get("error_message_included") else 0.0)
            + (1.0 if not is_vague else -1.0),
            0.0, 10.0,
        )
        pp_efficiency = _clamp(
            (5.0 if has_code_delta else 2.0)
            + (3.0 if wc <= 150 else 0.0)
            + (2.0 if has_context else 0.0),
            0.0, 10.0,
        )
        per_prompt.append({
            "clarity": round(pp_clarity, 1),
            "specificity": round(pp_specificity, 1),
            "efficiency": round(pp_efficiency, 1),
            "word_count": wc,
            "has_context": has_context,
            "is_vague": is_vague,
        })
    return per_prompt


# ---------------------------------------------------------------------------
# Fraud detection
# ---------------------------------------------------------------------------

def _detect_fraud(
    prompts: list,
    total_duration_seconds: int,
    tests_passed: int,
) -> Dict[str, Any]:
    total = len(prompts) or 1
    paste_ratio = _bool_rate(prompts, "paste_detected")
    external_paste = any((p.get("paste_length") or 0) > 400 for p in prompts)
    solution_dump = any(_is_solution_dump(p.get("message", "")) for p in prompts)
    injection = any(
        any(re.search(pat, (p.get("message", "") or "").lower()) for pat in INJECTION_PATTERNS)
        for p in prompts
    )
    suspiciously_fast = (total_duration_seconds < 300 and tests_passed > 0)
    first_sec = ((prompts[0].get("time_since_assessment_start_ms") or 0) / 1000.0) if prompts else 999

    deltas = [abs((p.get("code_diff_lines_added") or 0) + (p.get("code_diff_lines_removed") or 0)) for p in prompts]
    zero_change = sum(1 for d in deltas if d == 0)

    flags = []
    if paste_ratio > 0.70:
        flags.append("paste_ratio_above_70_percent")
    if external_paste:
        flags.append("external_paste_detected")
    if solution_dump:
        flags.append("solution_dump_detected")
    if injection:
        flags.append("injection_attempt")
    if suspiciously_fast:
        flags.append("suspiciously_fast")
    if first_sec < 30:
        flags.append("first_prompt_within_30_seconds")
    if total >= 3 and zero_change >= 3:
        flags.append("zero_code_changes_after_3plus_prompts")
    if any(_count_words(p.get("message", "")) > 1000 for p in prompts):
        flags.append("single_prompt_above_1000_words")

    return {
        "flags": flags,
        "paste_ratio": round(paste_ratio, 3),
        "external_paste_detected": external_paste,
        "solution_dump_detected": solution_dump,
        "injection_attempt": injection,
        "suspiciously_fast": suspiciously_fast,
    }


# ---------------------------------------------------------------------------
# Legacy component scores (backward compatibility)
# ---------------------------------------------------------------------------

def _build_legacy_component_scores(
    cat_results: Dict[str, Dict],
    prompts: list,
    tests_passed: int,
    tests_total: int,
    total_tokens: int,
    duration_minutes: float,
    time_limit_minutes: int,
) -> Dict[str, float]:
    """Build the old 12-component score dict (0-100) for backward compat."""
    tc = cat_results["task_completion"]["detailed"]
    pc = cat_results["prompt_clarity"]["detailed"]
    cp = cat_results["context_provision"]["detailed"]
    ind = cat_results["independence"]["detailed"]
    util = cat_results["utilization"]["detailed"]
    app = cat_results["approach"]["detailed"]
    comm = cat_results["communication"]["detailed"]

    return {
        "tests_passed_ratio": round(tc["tests_passed_ratio"] * 10, 2),
        "time_efficiency": round(tc["time_efficiency"] * 10, 2),
        "completion_time": round(tc["time_compliance"] * 10, 2),
        "clarity_score": round(pc["prompt_length_quality"] * 10, 2),
        "context_score": round(cp["code_context_rate"] * 10, 2),
        "specificity_score": round(pc["prompt_specificity"] * 10, 2),
        "independence_score": round(ind["first_prompt_delay"] * 10, 2),
        "efficiency_score": round(ind["prompt_efficiency"] * 10, 2),
        "iteration_score": round(util["iteration_quality"] * 10, 2),
        "response_utilization_score": round(util["post_prompt_changes"] * 10, 2),
        "decomposition_score": round(app["design_score"] * 10, 2),
        "code_quality_score": round(comm["grammar_score"] * 10, 2),
    }
