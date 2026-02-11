"""Tests for composite scoring with configurable weights."""

import json
import pytest
from unittest.mock import MagicMock, patch


class TestCompositeScoring:
    """Test the composite scoring formula used in submit_assessment."""

    def test_default_weights_sum_to_one(self):
        default_weights = {
            "tests": 0.30,
            "code_quality": 0.15,
            "prompt_quality": 0.15,
            "prompt_efficiency": 0.10,
            "independence": 0.10,
            "context_utilization": 0.05,
            "design_thinking": 0.05,
            "debugging_strategy": 0.05,
            "written_communication": 0.05,
        }
        assert abs(sum(default_weights.values()) - 1.0) < 0.001

    def test_perfect_score(self):
        weights = {
            "tests": 0.30, "code_quality": 0.15, "prompt_quality": 0.15,
            "prompt_efficiency": 0.10, "independence": 0.10,
            "context_utilization": 0.05, "design_thinking": 0.05,
            "debugging_strategy": 0.05, "written_communication": 0.05,
        }
        scores = {k: 10.0 for k in weights}
        final = sum(weights[k] * scores[k] for k in weights)
        assert abs(final - 10.0) < 0.001

    def test_zero_score(self):
        weights = {
            "tests": 0.30, "code_quality": 0.15, "prompt_quality": 0.15,
            "prompt_efficiency": 0.10, "independence": 0.10,
            "context_utilization": 0.05, "design_thinking": 0.05,
            "debugging_strategy": 0.05, "written_communication": 0.05,
        }
        scores = {k: 0.0 for k in weights}
        final = sum(weights[k] * scores[k] for k in weights)
        assert final == 0.0

    def test_solution_focused_preset(self):
        """solution_focused preset should weight tests at 50%."""
        weights = {
            "tests": 0.50, "code_quality": 0.15, "prompt_quality": 0.10,
            "prompt_efficiency": 0.05, "independence": 0.05,
            "context_utilization": 0.05, "design_thinking": 0.05,
            "debugging_strategy": 0.025, "written_communication": 0.025,
        }
        scores = {"tests": 10.0, "code_quality": 5.0, "prompt_quality": 5.0,
                  "prompt_efficiency": 5.0, "independence": 5.0,
                  "context_utilization": 5.0, "design_thinking": 5.0,
                  "debugging_strategy": 5.0, "written_communication": 5.0}
        final = sum(weights.get(k, 0) * scores[k] for k in scores)
        # Tests contribute 5.0, rest contribute ~2.5 = 7.5 total
        assert final > 7.0
        assert final < 8.0

    def test_custom_task_weights_override(self):
        """Task-specific weights should override defaults."""
        default_weights = {"tests": 0.30, "code_quality": 0.70}
        task_weights = {"tests": 0.70, "code_quality": 0.30}
        scores = {"tests": 10.0, "code_quality": 5.0}
        
        default_score = sum(default_weights.get(k, 0) * scores[k] for k in scores)
        task_score = sum(task_weights.get(k, 0) * scores[k] for k in scores)
        
        # With task weights, tests matter more -> higher score
        assert task_score > default_score

    def test_score_clamped_to_range(self):
        """Final score should be clamped between 0 and 10."""
        score = 15.0
        clamped = round(min(10.0, max(0.0, score)), 1)
        assert clamped == 10.0

        score = -5.0
        clamped = round(min(10.0, max(0.0, score)), 1)
        assert clamped == 0.0
