"""Unit tests for the ``interrogation_outcome`` rubric grader.

The grader is deterministic — no Anthropic call. Tests pin:
- the score-bucket mapping (all-resolved → excellent, half → good,
  any-dodge → poor, etc.)
- the dispatch from ``grade_rubric`` based on the ``grader`` field
- the reasoning + evidence shape so the recruiter UI doesn't break
- graceful behaviour on missing or malformed decision_points
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.components.assessments.rubric_scoring import (
    RubricScorer,
    ScoringArtifacts,
)


def _dps() -> List[Dict[str, Any]]:
    return [
        {
            "id": "shape",
            "headline": "The shape.",
            "tension": "...",
            "options": [
                {"label": "A", "summary": "a"},
                {"label": "B", "summary": "b"},
            ],
            "ask": "pick one",
            "valid_commit": "names one and the cost",
        },
        {
            "id": "severity",
            "headline": "Severity.",
            "tension": "...",
            "options": [
                {"label": "WARN", "summary": "w"},
                {"label": "ERROR", "summary": "e"},
            ],
            "ask": "WARN or ERROR",
            "valid_commit": "names one with a reason",
        },
    ]


def _transcript(per_dp_per_turn: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Build a synthetic ai_prompts transcript where each turn records the
    given per-decision status payload."""
    return [
        {"message": f"turn {i}", "response": "...", "interrogation_state": {
            dp_id: {"status": status} for dp_id, status in turn.items()
        }}
        for i, turn in enumerate(per_dp_per_turn)
    ]


@pytest.fixture
def scorer() -> RubricScorer:
    # API key is required by the constructor but never used by the
    # interrogation grader (it's deterministic).
    return RubricScorer(api_key="sk-test", organization_id=1, assessment_id=99)


class TestInterrogationOutcomeGrader:
    def test_all_commit_is_excellent(self, scorer):
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "commit", "severity": "commit"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "excellent"
        assert grade.score == 9.5
        assert "2/2 resolved" in grade.reasoning

    def test_commit_plus_reframe_is_excellent(self, scorer):
        # Reframes are first-class — they count as resolved.
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "commit", "severity": "reframe"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "excellent"
        assert grade.score == 9.5

    def test_half_resolved_is_poor_not_good(self, scorer):
        # Judgment-first buckets (2026-06-02): owning 1 of 2 decisions is
        # PARTIAL steering and scores LOW (4.5/poor), not "good"/6.5. The
        # skill is steering EVERY decision.
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "commit", "severity": "vague"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "poor"
        assert grade.score == 4.5  # 2.0 + (1/2)·5

    def test_any_dodge_is_poor_regardless_of_other(self, scorer):
        # Even one dodge sinks the dim — the rubric is explicit that
        # delegating ANY decision back to Claude is a poor signal.
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "commit", "severity": "dodge"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "poor"
        assert grade.score == 1.5
        assert "1 dodge" in grade.reasoning

    def test_all_unaddressed_is_poor(self, scorer):
        artifacts = ScoringArtifacts(
            prompt_transcript=[],  # no engagement at all
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "poor"
        assert grade.score == 2.0  # 0 resolved, no dodge → 2.0 + 0

    def test_carry_forward_means_late_commit_still_counts(self, scorer):
        # Turn 1: vague on both. Turn 2: commit on both. Late commit
        # must still rate excellent (the merge-state semantics).
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "vague", "severity": "vague"},
                {"shape": "commit", "severity": "commit"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "excellent"

    def test_missing_decision_points_returns_error_grade(self, scorer):
        artifacts = ScoringArtifacts(
            prompt_transcript=[],
            decision_points=[],  # task has no decisions configured
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        assert grade.rating == "poor"
        assert grade.error == "missing_decision_points"

    def test_evidence_cites_transcript_turns(self, scorer):
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "vague", "severity": "vague"},
                {"shape": "commit", "severity": "commit"},
            ]),
            decision_points=_dps(),
        )
        grade = scorer.grade_dimension_via_interrogation_outcome(
            "design_decisions_articulated", artifacts, weight=0.3,
        )
        # Both decisions reached commit at turn 2 (1-indexed in the
        # citation text). At least one evidence string must reflect that.
        assert any("turn 2" in c for c in grade.evidence_citations)


class TestGraderDispatchInGradeRubric:
    def test_dispatches_to_interrogation_grader_when_grader_field_set(self, scorer):
        rubric = {
            "design_decisions_articulated": {
                "weight": 0.3,
                "grader": "interrogation_outcome",
            },
            # Use a heuristic dim with weight=0 so we don't need a real
            # Anthropic call here.
            "other_dim": {"weight": 0.0, "criteria": {}},
        }
        artifacts = ScoringArtifacts(
            prompt_transcript=_transcript([
                {"shape": "commit", "severity": "commit"},
            ]),
            decision_points=_dps(),
        )
        # The "other_dim" criteria grader would call Anthropic. Stub via
        # monkeypatching the method since we only care about dispatch
        # here.
        scorer.grade_dimension = lambda dim_id, criteria, artifacts, *, weight, lens=None: type(
            "G", (), {"dimension_id": dim_id, "score": 0.0, "rating": "poor",
                      "reasoning": "", "evidence_citations": [], "weight": weight,
                      "error": None}
        )()
        result = scorer.grade_rubric(rubric, artifacts)
        by_id = {d.dimension_id: d for d in result.dimensions}
        assert by_id["design_decisions_articulated"].rating == "excellent"
        assert by_id["design_decisions_articulated"].score == 9.5


class TestLensAwareGrading:
    """Pin the lens model: decision lens punishes delegation, deliverable
    lens credits the shipped artifact regardless of who typed it."""

    def test_lens_selects_system_prompt(self):
        from app.components.assessments.rubric_scoring import (
            _system_prompt_for_lens,
            _DECISION_LENS_PROMPT,
            _DELIVERABLE_LENS_PROMPT,
        )
        assert _system_prompt_for_lens("decision") is _DECISION_LENS_PROMPT
        assert _system_prompt_for_lens("deliverable") is _DELIVERABLE_LENS_PROMPT
        # Unknown / missing → decision frame (back-compat).
        assert _system_prompt_for_lens(None) is _DECISION_LENS_PROMPT
        assert _system_prompt_for_lens("nonsense") is _DECISION_LENS_PROMPT

    def test_decision_prompt_punishes_delegation(self):
        from app.components.assessments.rubric_scoring import _DECISION_LENS_PROMPT
        assert "DECISION" in _DECISION_LENS_PROMPT
        assert "delegated" in _DECISION_LENS_PROMPT.lower()
        assert "design thinking" in _DECISION_LENS_PROMPT.lower()

    def test_deliverable_prompt_credits_agent_use(self):
        from app.components.assessments.rubric_scoring import _DELIVERABLE_LENS_PROMPT
        assert "DELIVERABLE" in _DELIVERABLE_LENS_PROMPT
        assert "do not penalise" in _DELIVERABLE_LENS_PROMPT.lower()
        assert "shipped" in _DELIVERABLE_LENS_PROMPT.lower()

    def test_tuned_buckets_full_table(self, scorer):
        # The validated bucket table (Sam, 2026-06-02).
        def sc(state):
            from app.components.assessments.rubric_scoring import ScoringArtifacts
            a = ScoringArtifacts(
                prompt_transcript=[{"interrogation_state": {k: {"status": v} for k, v in state.items()}}],
                decision_points=_dps(),
            )
            g = scorer.grade_dimension_via_interrogation_outcome("d", a, weight=0.4)
            return g.score
        assert sc({"shape": "commit", "severity": "reframe"}) == 9.5   # all resolved
        assert sc({"shape": "commit", "severity": "vague"}) == 4.5     # 1 of 2 owned
        assert sc({"shape": "vague", "severity": "vague"}) == 2.0      # none owned, no dodge
        assert sc({"shape": "commit", "severity": "dodge"}) == 1.5     # any dodge floors
