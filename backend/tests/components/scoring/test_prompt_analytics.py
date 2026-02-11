"""Tests for the heuristic prompt analytics module."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from app.services.prompt_analytics import (
    compute_time_to_first_prompt,
    compute_prompt_speed,
    compute_prompt_frequency,
    compute_prompt_length_stats,
    detect_copy_paste,
    compute_code_delta,
    compute_self_correction_rate,
    compute_token_efficiency,
    compute_browser_focus_ratio,
    compute_tab_switch_count,
    compute_all_heuristics,
)


def _make_assessment(**kwargs):
    """Create a mock assessment object."""
    a = MagicMock()
    a.started_at = kwargs.get("started_at", datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc))
    a.completed_at = kwargs.get("completed_at", datetime(2026, 1, 1, 10, 30, 0, tzinfo=timezone.utc))
    a.ai_prompts = kwargs.get("ai_prompts", [])
    a.tab_switch_count = kwargs.get("tab_switch_count", 0)
    a.tests_passed = kwargs.get("tests_passed", 3)
    a.tests_total = kwargs.get("tests_total", 5)
    return a


def _make_prompts(count=5, paste=False, focused=True):
    """Generate a list of mock prompt records."""
    base = datetime(2026, 1, 1, 10, 2, 0, tzinfo=timezone.utc)
    prompts = []
    for i in range(count):
        ts = base + timedelta(minutes=i * 3)
        prompts.append({
            "message": f"How do I fix bug number {i + 1} in the code?",
            "response": f"Here is a suggestion for bug {i + 1}...",
            "timestamp": ts.isoformat(),
            "input_tokens": 100 + i * 10,
            "output_tokens": 200 + i * 20,
            "tokens_used": 300 + i * 30,
            "response_latency_ms": 500 + i * 100,
            "code_before": f"def main():\n    # version {i}\n    pass",
            "code_after": f"def main():\n    # version {i + 1}\n    print('fixed')",
            "word_count": 10 + i,
            "char_count": 50 + i * 5,
            "time_since_last_prompt_ms": 180000 if i > 0 else None,
            "paste_detected": paste,
            "browser_focused": focused,
        })
    return prompts


class TestTimeToFirstPrompt:
    def test_basic(self):
        prompts = _make_prompts(1)
        assessment = _make_assessment(ai_prompts=prompts)
        result = compute_time_to_first_prompt(assessment)
        assert result["signal"] == "time_to_first_prompt"
        assert result["value"] == 120  # 2 minutes
        assert result["flag"] is None

    def test_rushed(self):
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        prompts = [{"message": "help", "timestamp": (base + timedelta(seconds=10)).isoformat()}]
        assessment = _make_assessment(ai_prompts=prompts, started_at=base)
        result = compute_time_to_first_prompt(assessment)
        assert result["value"] == 10
        assert result["flag"] == "rushed"

    def test_deliberate(self):
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        prompts = [{"message": "help", "timestamp": (base + timedelta(minutes=10)).isoformat()}]
        assessment = _make_assessment(ai_prompts=prompts, started_at=base)
        result = compute_time_to_first_prompt(assessment)
        assert result["value"] == 600
        assert result["flag"] == "deliberate"

    def test_no_prompts(self):
        assessment = _make_assessment(ai_prompts=[])
        result = compute_time_to_first_prompt(assessment)
        assert result["value"] is None


class TestPromptSpeed:
    def test_basic(self):
        result = compute_prompt_speed(_make_prompts(3))
        assert result["signal"] == "prompt_speed"
        assert result["avg_ms"] == 180000  # 3 min between each

    def test_single_prompt(self):
        result = compute_prompt_speed(_make_prompts(1))
        assert result["value"] is None


class TestPromptFrequency:
    def test_basic(self):
        result = compute_prompt_frequency(_make_prompts(5), 1800)
        assert result["total"] == 5
        assert result["flag"] is None

    def test_excessive(self):
        result = compute_prompt_frequency(_make_prompts(35), 1800)
        assert result["total"] == 35
        assert result["flag"] == "excessive"


class TestPromptLengthStats:
    def test_basic(self):
        result = compute_prompt_length_stats(_make_prompts(5))
        assert result["signal"] == "prompt_length_stats"
        assert result["avg_words"] > 0
        assert result["min_words"] > 0

    def test_empty(self):
        result = compute_prompt_length_stats([])
        assert result["avg_words"] == 0


class TestCopyPaste:
    def test_no_flags(self):
        result = detect_copy_paste(_make_prompts(3))
        assert result["paste_event_count"] == 0
        assert result["flag"] is None

    def test_paste_detected(self):
        result = detect_copy_paste(_make_prompts(3, paste=True))
        assert result["paste_event_count"] == 3

    def test_pattern_match(self):
        prompts = [{"message": "Here is the solution:\n" + "x = 1\n" * 5, "paste_detected": False}]
        result = detect_copy_paste(prompts)
        assert len(result["flags"]) > 0


class TestCodeDelta:
    def test_basic(self):
        result = compute_code_delta(_make_prompts(3))
        assert result["signal"] == "code_delta"
        assert result["prompts_with_code_change"] == 3
        assert result["utilization_rate"] == 1.0

    def test_no_change(self):
        prompts = [{"code_before": "x = 1", "code_after": "x = 1"}]
        result = compute_code_delta(prompts)
        assert result["prompts_with_code_change"] == 0


class TestSelfCorrection:
    def test_basic(self):
        prompts = _make_prompts(3)
        result = compute_self_correction_rate(prompts)
        assert result["signal"] == "self_correction_rate"
        # The code_before of prompt 2 differs from code_after of prompt 1
        assert result["rate"] is not None

    def test_single(self):
        result = compute_self_correction_rate(_make_prompts(1))
        assert result["rate"] is None


class TestTokenEfficiency:
    def test_basic(self):
        result = compute_token_efficiency(_make_prompts(5), 3, 5)
        assert result["signal"] == "token_efficiency"
        assert result["total_tokens"] > 0
        assert result["solve_rate"] == 0.6


class TestBrowserFocus:
    def test_all_focused(self):
        result = compute_browser_focus_ratio(_make_prompts(5, focused=True), 1800)
        assert result["ratio"] == 1.0
        assert result["flag"] is None

    def test_all_unfocused(self):
        result = compute_browser_focus_ratio(_make_prompts(5, focused=False), 1800)
        assert result["ratio"] == 0.0
        assert result["flag"] == "very_low_focus"


class TestTabSwitchCount:
    def test_basic(self):
        assessment = _make_assessment(tab_switch_count=3)
        result = compute_tab_switch_count(assessment)
        assert result["count"] == 3
        assert result["flag"] is None

    def test_excessive(self):
        assessment = _make_assessment(tab_switch_count=15)
        result = compute_tab_switch_count(assessment)
        assert result["flag"] == "excessive_switching"


class TestComputeAllHeuristics:
    def test_returns_all_signals(self):
        prompts = _make_prompts(5)
        assessment = _make_assessment(ai_prompts=prompts, tab_switch_count=2)
        result = compute_all_heuristics(assessment, prompts)
        assert "time_to_first_prompt" in result
        assert "prompt_speed" in result
        assert "prompt_frequency" in result
        assert "prompt_length_stats" in result
        assert "copy_paste_detection" in result
        assert "code_delta" in result
        assert "self_correction_rate" in result
        assert "token_efficiency" in result
        assert "browser_focus_ratio" in result
        assert "tab_switch_count" in result
        assert "_summary" in result
